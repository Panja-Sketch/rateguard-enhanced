"""Durable, bounded coordination of a connector-backed portfolio impact scan.

The coordinator never prices a policy itself. It (1) plans a deterministic set
of batches and records the job idempotently, (2) dispatches batches with
back-pressure (at most `max_inflight_batches` outstanding; stalled batches are
re-dispatched, which is safe because batch handling is idempotent), (3) polls
the checkpoints, honouring cancellation and the mission time budget, and
(4) finalizes exactly once through a transactional first-writer-wins write.

If the coordinating worker dies, the redelivered mission resumes the same job:
completed batches are never recomputed.
"""

from __future__ import annotations

import hashlib
import logging
import math
import random
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from app.connectors.contract import BATCH_MAX_ITEMS
from app.connectors.errors import ConnectorException
from app.impact.aggregate import ImpactAggregate, aggregate_job
from app.impact.config import ImpactConfig, budget_snapshot
from app.impact.dispatch import BatchDispatcher
from app.impact.models import BatchRecord, BatchState, ImpactJob, ImpactStatus, compute_job_id
from app.impact.snapshot import PortfolioSnapshot, load_snapshot
from app.ipir.package import IPIRPackage

logger = logging.getLogger(__name__)

# A batch whose every row failed permanently (and none matched/affected) points
# at a systemic problem (auth, version, schema); stop scanning after this many.
HALT_AFTER_PERMANENT_FAILED_BATCHES = 3


def package_identity(package: IPIRPackage) -> str:
    return hashlib.sha256(package.model_dump_json().encode("utf-8")).hexdigest()


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class ConnectorImpactCoordinator:
    def __init__(
        self,
        store,
        config: ImpactConfig,
        dispatcher: BatchDispatcher,
        *,
        snapshot_loader: Callable[[str], PortfolioSnapshot] = load_snapshot,
        capability_probe: Callable[[str, str], int] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        today: Callable[[], date] = date.today,
    ) -> None:
        self._store = store
        self._config = config
        self._dispatcher = dispatcher
        self._snapshot_loader = snapshot_loader
        self._capability_probe = capability_probe
        self._clock = clock
        self._sleep = sleep
        self._wall = wall_clock
        self._today = today

    # -- planning --------------------------------------------------------------------

    def plan(
        self,
        *,
        tenant_id: str,
        mission_id: str,
        attempt_number: int,
        dataset: str,
        sample_limit: int | None,
        package: IPIRPackage,
        source_ref: dict,
        connector_id: str,
        engine_version: str,
        product: str,
        jurisdiction: str | None,
    ) -> tuple[ImpactJob, PortfolioSnapshot]:
        cfg = self._config
        snapshot = self._snapshot_loader(dataset)
        rows_total = snapshot.row_count
        cap = min(cfg.max_policies, sample_limit) if sample_limit else cfg.max_policies
        rows_in_scope = min(rows_total, cap)
        batch_count = math.ceil(rows_in_scope / cfg.batch_size) if rows_in_scope else 0
        src_sha = package_identity(package)
        job_id = compute_job_id(
            tenant_id, mission_id, attempt_number, snapshot.sha256, src_sha,
            connector_id, engine_version, cfg.batch_size, rows_in_scope,
        )
        existing = self._store.get_job(tenant_id, job_id)
        if existing is not None:
            return existing, snapshot

        batch_max = 0
        if self._capability_probe is not None:
            try:
                batch_max = max(0, min(int(self._capability_probe(connector_id, engine_version)), BATCH_MAX_ITEMS))
            except ConnectorException:
                batch_max = 0
        job = ImpactJob(
            tenant_id=tenant_id, job_id=job_id, mission_id=mission_id, attempt_number=attempt_number,
            as_of=self._today().isoformat(), status=ImpactStatus.RUNNING, product=product,
            jurisdiction=jurisdiction, snapshot=snapshot.identity(),
            source={**source_ref, "ipir_sha256": src_sha, "package_id": package.id},
            connector={"connector_id": connector_id, "engine_version": engine_version,
                       "batch_max_items": batch_max},
            batch_size=cfg.batch_size, batch_count=batch_count,
            rows_total=rows_total, rows_in_scope=rows_in_scope, budgets=budget_snapshot(cfg),
        )
        return self._store.create_job(job), snapshot

    # -- execution ---------------------------------------------------------------------

    def run(
        self,
        job: ImpactJob,
        snapshot: PortfolioSnapshot,
        *,
        declared_input_ids: set[str] | None,
        cancellation_check: Callable[[], bool],
        heartbeat: Callable[[], None],
    ) -> ImpactAggregate:
        cfg = self._config
        tenant_id, job_id = job.tenant_id, job.job_id
        if job.finalized_at and job.aggregate:
            return ImpactAggregate.model_validate(job.aggregate)  # duplicate/late completion: stored result wins

        specs = [
            (n, n * cfg.batch_size, min((n + 1) * cfg.batch_size, job.rows_in_scope))
            for n in range(job.batch_count)
        ]
        self._store.ensure_batches(tenant_id, job_id, specs)

        started = self._clock()
        deadline = started + cfg.max_mission_seconds
        dispatched: dict[int, datetime] = {}
        stall = timedelta(seconds=cfg.batch_timeout_seconds + 90)
        budget_exhausted: list[str] = list(job.budget_exhausted)
        halt_reason = job.halt_reason
        if job.rows_in_scope < job.rows_total and "ROW_BUDGET" not in budget_exhausted:
            budget_exhausted.append("ROW_BUDGET")

        last_signature: tuple | None = None
        last_change = self._clock()
        stall_logged = False
        while True:
            try:
                heartbeat()
            except Exception:  # noqa: BLE001 - liveness bookkeeping must never abort the scan
                logger.warning("IMPACT_HEARTBEAT_FAILED job=%s", job_id)
            if cancellation_check():
                self._store.request_cancel(tenant_id, job_id)
                break

            batches = self._store.list_batches(tenant_id, job_id)
            now = self._wall()
            self._publish_progress(job, batches, self._clock() - started)
            signature = tuple((b.batch_no, b.state.value, b.attempts) for b in batches)
            if signature != last_signature:
                last_signature, last_change, stall_logged = signature, self._clock(), False
            elif not stall_logged and self._clock() - last_change > cfg.batch_timeout_seconds / 2:
                logger.warning("IMPACT_STALLED no_batch_progress_seconds=%d", int(self._clock() - last_change))
                stall_logged = True
            if all(self._settled(b, now) for b in batches):
                break
            if self._systemic_permanent_failure(batches):
                halt_reason = "CONNECTOR_PERMANENT_FAILURE"
                break
            if self._clock() >= deadline:
                budget_exhausted.append("MAX_MISSION_SECONDS")
                break

            inflight = sum(1 for b in batches if self._in_flight(b, dispatched, now, stall))
            for b in batches:
                if inflight >= cfg.max_inflight_batches:
                    break
                if self._dispatchable(b, dispatched, now, stall):
                    try:
                        self._dispatcher.dispatch(tenant_id, job_id, b.batch_no)
                    except Exception:  # noqa: BLE001 - retried on the next poll
                        logger.exception("IMPACT_DISPATCH_FAILED job=%s batch=%s", job_id, b.batch_no)
                        continue
                    dispatched[b.batch_no] = self._wall()
                    inflight += 1
            self._sleep(cfg.poll_interval_seconds)

        self._store.update_job(
            tenant_id, job_id, {"budget_exhausted": budget_exhausted, "halt_reason": halt_reason}
        )
        return self.finalize(job_id, tenant_id, snapshot, declared_input_ids, duration=self._clock() - started)

    def _publish_progress(self, job: ImpactJob, batches: list[BatchRecord], elapsed: float) -> None:
        done = sum(1 for b in batches if b.state == BatchState.DONE)
        progress = {
            "batches_total": job.batch_count,
            "batches_done": done,
            "batches_incomplete": sum(1 for b in batches if b.state == BatchState.INCOMPLETE),
            "rows_processed": sum(b.rows for b in batches if b.completed_at),
            "rows_in_scope": job.rows_in_scope,
            "rows_total": job.rows_total,
            "mismatches_so_far": sum(len(b.affected) for b in batches),
            "inconclusive_so_far": sum(len(b.inconclusive) for b in batches),
            "retries": sum(b.retries for b in batches),
            "elapsed_seconds": round(elapsed, 1),
            "updated_at": self._wall().isoformat(),
        }
        try:
            self._store.update_job(job.tenant_id, job.job_id, {"progress": progress})
        except Exception:  # noqa: BLE001 - progress is informational only
            logger.warning("IMPACT_PROGRESS_WRITE_FAILED job=%s", job.job_id)

    def finalize(
        self, job_id: str, tenant_id: str, snapshot: PortfolioSnapshot,
        declared_input_ids: set[str] | None, *, duration: float | None = None,
    ) -> ImpactAggregate:
        job = self._store.get_job(tenant_id, job_id)
        if job is None:
            raise LookupError("impact job not found")
        if job.finalized_at and job.aggregate:
            return ImpactAggregate.model_validate(job.aggregate)
        batches = self._store.list_batches(tenant_id, job_id)
        agg = aggregate_job(job, batches, snapshot, self._config, declared_input_ids, duration_seconds=duration)
        stored, created = self._store.finalize_job(tenant_id, job_id, agg.status, agg.model_dump(mode="json"))
        if not created and stored is not None and stored.aggregate:
            return ImpactAggregate.model_validate(stored.aggregate)  # lost a finalize race: winner's result stands
        return agg

    # -- batch state helpers ---------------------------------------------------------------

    def _settled(self, b: BatchRecord, now: datetime) -> bool:
        if b.state == BatchState.DONE:
            return True
        if b.attempts >= self._config.max_batch_attempts:
            expires = _parse(b.lease_expires_at)
            return not (b.state == BatchState.LEASED and expires is not None and expires > now)
        return False

    def _in_flight(self, b: BatchRecord, dispatched: dict[int, datetime], now: datetime, stall: timedelta) -> bool:
        if b.state == BatchState.LEASED:
            expires = _parse(b.lease_expires_at)
            return expires is not None and expires > now
        sent = dispatched.get(b.batch_no)
        updated = _parse(b.updated_at)
        return (
            sent is not None and (updated is None or updated <= sent) and (now - sent) < stall
            and b.state != BatchState.DONE
        )

    def _dispatchable(self, b: BatchRecord, dispatched: dict[int, datetime], now: datetime, stall: timedelta) -> bool:
        if self._settled(b, now) or self._in_flight(b, dispatched, now, stall):
            return False
        if b.state == BatchState.INCOMPLETE and b.completed_at:
            # Bounded exponential backoff with jitter between re-attempts of a batch.
            wait = min(30.0, 2.0 ** b.attempts) * (0.5 + random.random() / 2)
            done_at = _parse(b.completed_at)
            if done_at is not None and (now - done_at).total_seconds() < wait:
                return False
        return True

    @staticmethod
    def _systemic_permanent_failure(batches: list[BatchRecord]) -> bool:
        if any(b.matched or b.affected for b in batches):
            return False
        failed = [
            b for b in batches
            if b.state == BatchState.DONE and b.completed_at and b.inconclusive
            and len(b.inconclusive) + len(b.out_of_scope) >= b.rows
        ]
        return len(failed) >= HALT_AFTER_PERMANENT_FAILED_BATCHES
