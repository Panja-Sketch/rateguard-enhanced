"""Wiring of the impact subsystem to real dependencies (settings, artifact
store, Pub/Sub or local dispatch) for the mission supervisor and the worker's
batch endpoint."""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import threading
from collections.abc import Callable
from typing import Any

from app.connectors.client import ConnectorClient
from app.connectors.registry import select_connector
from app.core.config import get_settings
from app.impact.config import ImpactConfig, resolve_impact_config
from app.impact.coordinator import ConnectorImpactCoordinator, package_identity
from app.impact.dispatch import BatchDispatcher, LocalBatchDispatcher, PubSubBatchDispatcher
from app.impact.models import ImpactJob
from app.impact.processor import BatchProcessor
from app.impact.store import ImpactJobStore, get_impact_store
from app.ipir.package import IPIRPackage

logger = logging.getLogger(__name__)

_PACKAGE_CACHE: dict[tuple[str, str, str], IPIRPackage] = {}
_PACKAGE_LOCK = threading.Lock()
_MAX_CACHED_PACKAGES = 8


# Test hook: substitute the connector client (e.g. one wrapping an ASGI transport).
_CLIENT_FACTORY: Callable[..., ConnectorClient] | None = None


def set_connector_client_factory(factory: Callable[..., ConnectorClient] | None) -> None:
    global _CLIENT_FACTORY
    _CLIENT_FACTORY = factory


@functools.lru_cache(maxsize=1)
def get_impact_config() -> ImpactConfig:
    return resolve_impact_config()


def load_authoritative_package(job: ImpactJob) -> IPIRPackage:
    """Loads the job's authoritative IPIR from the tenant's own artifact prefix
    (or the built-in demo/sample resolver) and verifies it is byte-for-byte the
    package the job was planned against."""
    from app.models.mission import PricingSourceRef
    from app.services.mission_execution_service import _resolve_source_package

    key = (job.tenant_id, job.source["source_id"], job.source["ipir_sha256"])
    with _PACKAGE_LOCK:
        cached = _PACKAGE_CACHE.get(key)
    if cached is not None:
        return cached
    ref = PricingSourceRef(
        source_id=job.source["source_id"], source_type=job.source["source_type"], name="Source A"
    )
    package = _resolve_source_package(ref, job.tenant_id)
    if package is None or package_identity(package) != job.source["ipir_sha256"]:
        raise RuntimeError("Authoritative source identity does not match the impact job.")
    with _PACKAGE_LOCK:
        if len(_PACKAGE_CACHE) >= _MAX_CACHED_PACKAGES:
            _PACKAGE_CACHE.clear()
        _PACKAGE_CACHE[key] = package
    return package


def build_processor(store: ImpactJobStore | None = None, config: ImpactConfig | None = None) -> BatchProcessor:
    return BatchProcessor(
        store or get_impact_store(),
        config or get_impact_config(),
        package_loader=load_authoritative_package,
        client_factory=(lambda on_retry: _CLIENT_FACTORY(on_retry=on_retry)) if _CLIENT_FACTORY else None,
    )


def _capability_probe(config: ImpactConfig):
    def probe(connector_id: str, engine_version: str) -> int:
        entry = select_connector(connector_id, engine_version)
        client = (
            _CLIENT_FACTORY(on_retry=None)
            if _CLIENT_FACTORY
            else ConnectorClient(
                max_attempts=config.max_retry_attempts, request_timeout_seconds=config.request_timeout_seconds
            )
        )
        return asyncio.run(client.discover_batch_capability(entry))

    return probe


def build_dispatcher(processor: BatchProcessor, config: ImpactConfig) -> BatchDispatcher:
    settings = get_settings()
    deployed = os.environ.get("RATEGUARD_RUN_STORE", "").lower() == "firestore"
    if deployed and settings.execution_mode == "pubsub" and not os.environ.get("RATEGUARD_IMPACT_LOCAL_DISPATCH"):
        return PubSubBatchDispatcher(
            settings.google_cloud_project, os.environ.get("RATEGUARD_IMPACT_TOPIC", "impact-batches")
        )
    return LocalBatchDispatcher(processor.process, max_workers=config.max_inflight_batches)


def build_coordinator(
    store: ImpactJobStore | None = None, config: ImpactConfig | None = None
) -> tuple[ConnectorImpactCoordinator, BatchDispatcher]:
    config = config or get_impact_config()
    store = store or get_impact_store()
    processor = build_processor(store, config)
    dispatcher = build_dispatcher(processor, config)
    return (
        ConnectorImpactCoordinator(store, config, dispatcher, capability_probe=_capability_probe(config)),
        dispatcher,
    )


def process_batch_message(tenant_id: str, job_id: str, batch_no: int, owner: str) -> str:
    """Worker entry point for one Pub/Sub batch delivery."""
    return build_processor().process(tenant_id, job_id, batch_no, owner)


def summarize_for_log(agg: Any) -> str:
    """Aggregate counters only - safe to log."""
    return (
        f"status={agg.status.value} decision={agg.impact_decision} coverage_pct={agg.coverage_pct} "
        f"mismatches={agg.mismatches} inconclusive={agg.inconclusive} retries={agg.retry_count} "
        f"batches={agg.batches_done}/{agg.batch_count} wall_seconds={agg.budget.get('wall_seconds')}"
    )
