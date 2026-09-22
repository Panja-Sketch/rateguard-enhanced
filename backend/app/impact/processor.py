"""Idempotent processing of one impact batch.

For each row of the batch's deterministic slice: build the allowlisted masked
rating inputs, resolve the calculation date, compute the *expected* premium
locally through the authoritative IPIR, obtain the *candidate* premium from the
connector (batch quote when the connector advertises it, otherwise bounded
concurrent single quotes), and classify the row MATCH / AFFECTED /
INCONCLUSIVE using Decimal arithmetic and the configured tolerance.

The batch checkpoint is written exactly once by the current lease owner
(fenced), so Pub/Sub redelivery, duplicate delivery and worker restarts can
never double-count a row. A re-attempt of an INCOMPLETE batch only re-runs its
transient-failure rows and merges into the retained results.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.connectors.budget import TargetBudget
from app.connectors.client import ConnectorClient
from app.connectors.contract import (
    ConnectorBatchItem,
    ConnectorBatchRequest,
    ConnectorQuoteRequest,
)
from app.connectors.errors import ConnectorException, ConnectorFailureCategory
from app.connectors.registry import ConnectorRegistryEntry, select_connector
from app.engines.oracle.calculator import PremiumOracleCalculator
from app.engines.oracle.errors import CalculationDateError
from app.impact.config import ImpactConfig
from app.impact.models import BatchRecord, BatchState, ImpactJob, LeaseResult
from app.impact.resilience import (
    CIRCUIT_OPEN_CODE,
    TRANSIENT_ITEM_CODES,
    AsyncPacer,
    CircuitBreaker,
    is_transient,
)
from app.impact.snapshot import PortfolioSnapshot, load_snapshot, rating_inputs
from app.ipir.package import IPIRPackage

logger = logging.getLogger(__name__)

ClientFactory = Callable[[Callable[[str], None]], ConnectorClient]
PackageLoader = Callable[[ImpactJob], IPIRPackage]


class BatchOutcome:
    DONE = "DONE"
    INCOMPLETE = "INCOMPLETE"
    DUPLICATE = "DUPLICATE"  # already DONE, or leased by another live owner
    CLOSED = "CLOSED"  # job cancelled / finalized / missing
    EXHAUSTED = "EXHAUSTED"  # attempts exhausted; left to the coordinator
    LOST_LEASE = "LOST_LEASE"  # result rejected by the fencing check


@dataclass
class _Row:
    index: int
    kind: str  # MATCH | AFFECTED | INCONCLUSIVE | OUT_OF_SCOPE
    expected: str | None = None
    candidate: str | None = None
    code: str | None = None
    transient: bool = False


# Extracts only the leading "Input '<id>'" (or "'<id>' references"/"unknown
# table '<id>'") portion of an oracle error message -- enough to diagnose
# *which* field or node is systematically failing across a portfolio scan,
# without ever logging the specific policy value that triggered it.
_FIELD_HINT_PATTERN = re.compile(r"^(.*?'[A-Za-z0-9_]+')")


def _safe_error_hint(exc: Exception) -> str:
    match = _FIELD_HINT_PATTERN.match(str(exc))
    return match.group(1) if match else ""


def _norm(value: str | None) -> str:
    return (value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _connector_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in inputs.items()}


class BatchProcessor:
    def __init__(
        self,
        store,
        config: ImpactConfig,
        *,
        package_loader: PackageLoader,
        client_factory: ClientFactory | None = None,
        snapshot_loader: Callable[[str], PortfolioSnapshot] = load_snapshot,
        entry_resolver: Callable[[str, str], ConnectorRegistryEntry] = select_connector,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._config = config
        self._package_loader = package_loader
        self._client_factory = client_factory or (
            lambda on_retry: ConnectorClient(
                max_attempts=config.max_retry_attempts,
                request_timeout_seconds=config.request_timeout_seconds,
                on_retry=on_retry,
            )
        )
        self._snapshot_loader = snapshot_loader
        self._entry_resolver = entry_resolver
        self._clock = clock

    # -- public entry point ------------------------------------------------------

    def process(self, tenant_id: str, job_id: str, batch_no: int, owner: str) -> str:
        cfg = self._config
        lease_seconds = cfg.batch_timeout_seconds + 60
        outcome, batch = self._store.lease_batch(
            tenant_id, job_id, batch_no, owner, lease_seconds, cfg.max_batch_attempts
        )
        if outcome == LeaseResult.ACQUIRED and batch is not None:
            pass
        elif outcome in (LeaseResult.ALREADY_DONE, LeaseResult.LEASED_ELSEWHERE):
            return BatchOutcome.DUPLICATE
        elif outcome == LeaseResult.ATTEMPTS_EXHAUSTED:
            logger.warning("IMPACT_RETRY_EXHAUSTED batch_attempts=%s", cfg.max_batch_attempts)
            return BatchOutcome.EXHAUSTED
        else:  # NOT_FOUND / JOB_CLOSED
            return BatchOutcome.CLOSED

        job = self._store.get_job(tenant_id, job_id)
        if job is None:
            return BatchOutcome.CLOSED
        started = self._clock()
        try:
            result = self._execute(job, batch, owner)
        except Exception:
            # Release the lease without changing retained results so the
            # coordinator (or Pub/Sub redelivery) can re-dispatch promptly.
            self._store.complete_batch(
                tenant_id, job_id, batch_no, owner,
                {"duration_ms": int((self._clock() - started) * 1000)}, BatchState.INCOMPLETE,
            )
            raise
        result["duration_ms"] = int((self._clock() - started) * 1000)
        state = BatchState.INCOMPLETE if any(r[2] for r in result["inconclusive"]) else BatchState.DONE
        written = self._store.complete_batch(tenant_id, job_id, batch_no, owner, result, state)
        logger.info(
            "IMPACT_BATCH_DONE state=%s duration_ms=%s requests=%s retries=%s breaker_opened=%s",
            state.value, result["duration_ms"], result["requests"], result["retries"], result["breaker_opened"],
        )
        if not written:
            logger.warning("IMPACT_BATCH_LOST_LEASE batch=%s job=%s", batch_no, job_id)
            return BatchOutcome.LOST_LEASE
        return BatchOutcome.DONE if state == BatchState.DONE else BatchOutcome.INCOMPLETE

    # -- batch execution -----------------------------------------------------------

    def _execute(self, job: ImpactJob, batch: BatchRecord, owner: str) -> dict[str, Any]:
        snapshot = self._snapshot_loader(job.snapshot["dataset"])
        if snapshot.sha256 != job.snapshot["sha256"]:
            raise RuntimeError("Portfolio snapshot identity changed since the job was planned.")
        package = self._package_loader(job)
        declared = {inp.id for inp in package.inputs}
        calculator = PremiumOracleCalculator(package)

        first_pass = batch.completed_at is None
        indexes = (
            list(range(batch.row_start, batch.row_end)) if first_pass else sorted(batch.unresolved_transient)
        )
        # Retained results from the previous attempt (never recounted).
        matched = batch.matched if not first_pass else 0
        out_of_scope = list(batch.out_of_scope) if not first_pass else []
        affected = list(batch.affected) if not first_pass else []
        retained_inconclusive = [r for r in batch.inconclusive if not r[2]] if not first_pass else []

        pending: list[tuple[int, dict[str, Any], Any, str, Decimal]] = []  # idx, inputs, date, txn, expected
        rows: list[_Row] = []
        for idx in indexes:
            policy = snapshot.rows[idx]
            if _norm(policy.product_id) != _norm(job.product):
                rows.append(_Row(idx, "OUT_OF_SCOPE", code="PRODUCT_MISMATCH"))
                continue
            inputs = rating_inputs(policy, declared)
            try:
                calc = calculator.calculate_policy_premium(
                    inputs, effective_date=policy.effective_date, transaction_type=policy.transaction_type
                )
            except CalculationDateError as exc:
                if exc.code == "CALCULATION_DATE_OUT_OF_PERIOD":
                    # The authoritative source is not effective on this policy's
                    # date, so no authoritative expected premium exists: disclosed
                    # as out of scope, never priced and never counted as a match.
                    rows.append(_Row(idx, "OUT_OF_SCOPE", code="OUTSIDE_EFFECTIVE_PERIOD"))
                else:
                    rows.append(_Row(idx, "INCONCLUSIVE", code=exc.code))
                continue
            except Exception as exc:  # noqa: BLE001 - never leak input values
                logger.warning(
                    "IMPACT_LOCAL_CALC_ERROR error_type=%s hint=%s",
                    type(exc).__name__, _safe_error_hint(exc),
                )
                rows.append(_Row(idx, "INCONCLUSIVE", code="LOCAL_CALCULATION_ERROR"))
                continue
            pending.append((idx, inputs, calc.calculation_date, policy.transaction_type.value, calc.final_premium))

        retries: list[str] = []
        stats: dict[str, Any] = {"requests": 0, "revision": None, "breaker_opened": False}
        remote = self._run_remote(job, pending, retries, stats)
        for (idx, _inputs, _date, _txn, expected) in pending:
            kind, candidate, code, transient = remote[idx]
            if kind == "PREMIUM":
                delta = candidate - expected
                if abs(delta) > self._config.premium_tolerance:
                    rows.append(_Row(idx, "AFFECTED", str(expected), str(candidate)))
                else:
                    rows.append(_Row(idx, "MATCH"))
            else:
                rows.append(_Row(idx, "INCONCLUSIVE", code=code, transient=transient))

        errors: Counter[str] = Counter()
        inconclusive = list(retained_inconclusive)
        for row in rows:
            if row.kind == "MATCH":
                matched += 1
            elif row.kind == "OUT_OF_SCOPE":
                out_of_scope.append([row.index, row.code])
            elif row.kind == "AFFECTED":
                affected.append([row.index, row.expected, row.candidate])
            else:
                inconclusive.append([row.index, row.code, row.transient])
                errors[row.code or "UNKNOWN"] += 1
        return {
            "matched": matched,
            "out_of_scope": out_of_scope,
            "affected": sorted(affected, key=lambda a: a[0]),
            "inconclusive": sorted(inconclusive, key=lambda r: r[0]),
            "retries": batch.retries + len(retries),
            "requests": batch.requests + stats["requests"],
            "error_classes": dict(Counter(batch.error_classes) + errors),
            "breaker_opened": bool(batch.breaker_opened or stats["breaker_opened"]),
            "connector_revision": stats["revision"] or batch.connector_revision,
        }

    # -- remote pricing --------------------------------------------------------------

    def _run_remote(self, job, pending, retries, stats) -> dict[int, tuple[str, Decimal | None, str | None, bool]]:
        if not pending:
            return {}
        return asyncio.run(self._remote(job, pending, retries, stats))

    async def _remote(self, job, pending, retries, stats):
        cfg = self._config
        try:
            entry = self._entry_resolver(job.connector["connector_id"], job.connector["engine_version"])
        except ConnectorException as exc:  # unregistered connector / undeclared version: permanent, systemic
            return {i[0]: ("ERROR", None, exc.error.code, False) for i in pending}
        client = self._client_factory(retries.append)
        breaker = CircuitBreaker(cfg.breaker_threshold, cfg.breaker_cooldown_seconds, self._clock)
        pacer = AsyncPacer(cfg.per_batch_qps)
        budget = TargetBudget(cfg.batch_timeout_seconds)
        sem = asyncio.Semaphore(cfg.request_concurrency_per_batch)
        correlation = job.job_id
        results: dict[int, tuple[str, Decimal | None, str | None, bool]] = {}

        # Capability discovered once when the job was planned (recorded in the job).
        batch_max = int(job.connector.get("batch_max_items") or 0)
        chunk = max(1, min(batch_max, cfg.batch_size)) if batch_max else 1

        def fail(idxs, code, transient):
            for i in idxs:
                results[i] = ("ERROR", None, code, transient)

        cancel_state = {"checked_at": -1e9, "cancelled": False}

        async def cancelled() -> bool:
            # Re-read the job's cancel flag at most every 2 seconds.
            if self._clock() - cancel_state["checked_at"] >= 2.0:
                cancel_state["checked_at"] = self._clock()
                current = await asyncio.to_thread(self._store.get_job, job.tenant_id, job.job_id)
                cancel_state["cancelled"] = current is None or current.cancel_requested
            return cancel_state["cancelled"]

        async def send_chunk(items):
            idxs = [i[0] for i in items]
            if await cancelled():
                fail(idxs, "IMPACT_CANCELLED", True)
                return
            if not breaker.allow():
                code = breaker.permanent_code or CIRCUIT_OPEN_CODE
                fail(idxs, code, breaker.permanent_code is None)
                return
            async with sem:
                await pacer.acquire()
                stats["requests"] += 1
                try:
                    if batch_max:
                        req = ConnectorBatchRequest(
                            batch_id=f"{job.job_id}:{items[0][0]}",
                            engine_version=job.connector["engine_version"],
                            product_id=job.product,
                            items=[
                                ConnectorBatchItem(
                                    item_id=f"r{i}", effective_date=d, transaction_type=t,
                                    inputs=_connector_inputs(inp),
                                )
                                for (i, inp, d, t, _e) in items
                            ],
                        )
                        resp = await client.send_quote_batch(entry, req, budget=budget, correlation_id=correlation)
                        stats["revision"] = resp.engine_revision or stats["revision"]
                        by_id = {r.item_id: r for r in resp.results}
                        transient_seen = False
                        for (i, *_rest) in items:
                            item = by_id[f"r{i}"]
                            if item.status == "OK":
                                results[i] = self._parse_premium(item.outputs.get("final_premium"))
                            else:
                                code = item.error.code if item.error else "ITEM_ERROR"
                                is_t = code in TRANSIENT_ITEM_CODES
                                transient_seen = transient_seen or is_t
                                results[i] = ("ERROR", None, f"CONNECTOR_ITEM_{code}", is_t)
                        if transient_seen and all(results[i][0] == "ERROR" for i in idxs):
                            breaker.record_failure("CONNECTOR_ITEM_TRANSIENT")
                        else:
                            breaker.record_success()
                    else:
                        (i, inp, d, t, _e) = items[0]
                        req = ConnectorQuoteRequest(
                            request_id=f"{job.job_id}:r{i}",
                            engine_version=job.connector["engine_version"],
                            product=job.product,
                            jurisdiction=job.jurisdiction,
                            effective_date=d,
                            transaction_type=t,
                            inputs=_connector_inputs(inp),
                        )
                        resp = await client.send_quote_to_entry(
                            entry, req, budget=budget, correlation_id=correlation
                        )
                        results[i] = self._parse_premium(resp.outputs.get("final_premium"))
                        breaker.record_success()
                except ConnectorException as exc:
                    transient = is_transient(exc)
                    code = exc.error.code
                    breaker.record_failure(code)
                    fail(idxs, code, transient)

        chunks = [pending[i : i + chunk] for i in range(0, len(pending), chunk)]
        await asyncio.gather(*(send_chunk(c) for c in chunks))
        if breaker.times_opened:
            stats["breaker_opened"] = True
        return results

    @staticmethod
    def _parse_premium(raw: str | None) -> tuple[str, Decimal | None, str | None, bool]:
        if raw is None or not raw.strip():
            return ("ERROR", None, "CONNECTOR_INCOMPLETE_OUTPUT", False)
        try:
            value = Decimal(raw)
        except InvalidOperation:
            return ("ERROR", None, "CONNECTOR_INVALID_PREMIUM", False)
        if not value.is_finite():
            return ("ERROR", None, "CONNECTOR_INVALID_PREMIUM", False)
        return ("PREMIUM", value, None, False)


__all__ = ["BatchOutcome", "BatchProcessor", "ConnectorFailureCategory"]
