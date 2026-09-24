"""Shared harness for connector-impact tests: a small deterministic snapshot,
the real demo rating engine over an in-process ASGI transport, and purpose-built
fake targets for failure modes. No network."""

from __future__ import annotations

from dataclasses import dataclass, replace

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.connectors.client import ConnectorClient
from app.connectors.registry import ConnectorRegistryEntry
from app.core.config import get_data_dir
from app.impact.config import ImpactConfig
from app.impact.coordinator import ConnectorImpactCoordinator
from app.impact.dispatch import LocalBatchDispatcher
from app.impact.processor import BatchProcessor
from app.impact.snapshot import PortfolioSnapshot, load_snapshot
from app.impact.store import InMemoryImpactStore
from app.ipir.v0_2.compat import lower_to_v0_1
from app.ipir.v0_2.package import IPIRPackageV2
from rating_engine.main import app as engine_app

ENTRY = ConnectorRegistryEntry(
    connector_id="c", display_name="c", base_url="http://127.0.0.1:9",
    allowed_engine_versions=("canonical-v1", "defective-v1"), is_local_dev=True,
)


async def _no_sleep(_: float) -> None:
    return None


def fast_config(**overrides) -> ImpactConfig:
    base = ImpactConfig(
        batch_size=50, poll_interval_seconds=0.01, max_mission_seconds=60, batch_timeout_seconds=30,
        max_qps=500.0, breaker_cooldown_seconds=1.0, max_retry_attempts=2,
    )
    return replace(base, **overrides)


@pytest.fixture(scope="session")
def package():
    # RateGuard's own approved-intent package (the IPIR fixture), independent of the
    # black-box engine, which no longer exposes any RateGuard package.
    fixture = get_data_dir() / "implementations" / "v0_2" / "canonical" / "AZ_HO3_GOLDEN_ipir.json"
    return lower_to_v0_1(IPIRPackageV2.model_validate_json(fixture.read_text(encoding="utf-8")))


@pytest.fixture(scope="session")
def snapshot() -> PortfolioSnapshot:
    """First 600 rows of the real synthetic portfolio (mixed in/out-of-period)."""
    real = load_snapshot()
    return PortfolioSnapshot(dataset=real.dataset, sha256="f" * 64, rows=real.rows[:600])


def status_app(status: int, counter: list[int] | None = None) -> Starlette:
    async def quote(request: Request):
        if counter is not None:
            counter.append(1)
        return JSONResponse({"detail": "x"}, status_code=status)

    async def caps(request: Request):
        return JSONResponse({}, status_code=404)

    return Starlette(routes=[Route("/quote", quote, methods=["POST"]), Route("/capabilities", caps)])


@dataclass
class Harness:
    store: InMemoryImpactStore
    coordinator: ConnectorImpactCoordinator
    processor: BatchProcessor
    dispatcher: LocalBatchDispatcher
    config: ImpactConfig

    def plan(self, snapshot, package, *, mission="MIS-T1", tenant="tenant-a", version="canonical-v1", attempt=1):
        return self.coordinator.plan(
            tenant_id=tenant, mission_id=mission, attempt_number=attempt, dataset=snapshot.dataset,
            sample_limit=None, package=package, source_ref={"source_id": "s", "source_type": "SAMPLE_RELEASE"},
            connector_id="c", engine_version=version, product="az_ho3", jurisdiction="Arizona",
        )

    def run(self, job, snapshot, package, *, cancel=lambda: False):
        return self.coordinator.run(
            job, snapshot, declared_input_ids={i.id for i in package.inputs},
            cancellation_check=cancel, heartbeat=lambda: None,
        )


def make_harness(snapshot, package, *, app=None, batch=True, config=None, store=None, dispatch_wrapper=None):
    config = config or fast_config()
    store = store or InMemoryImpactStore()
    transport = httpx.ASGITransport(app=app or engine_app)
    processor = BatchProcessor(
        store, config, package_loader=lambda job: package, snapshot_loader=lambda name: snapshot,
        client_factory=lambda on_retry: ConnectorClient(
            transport=transport, sleep_fn=_no_sleep, max_attempts=config.max_retry_attempts, on_retry=on_retry
        ),
        entry_resolver=lambda c, v: ENTRY,
    )
    process = dispatch_wrapper(processor.process) if dispatch_wrapper else processor.process
    dispatcher = LocalBatchDispatcher(process, config.max_inflight_batches)
    coordinator = ConnectorImpactCoordinator(
        store, config, dispatcher, snapshot_loader=lambda name: snapshot,
        capability_probe=(lambda c, v: 250) if batch else None, sleep=lambda s: __import__("time").sleep(s),
    )
    return Harness(store, coordinator, processor, dispatcher, config)
