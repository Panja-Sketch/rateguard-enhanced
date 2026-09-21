"""Deterministic aggregation of impact batches into one result.

Pure function of (job, batch records, portfolio snapshot): running it twice over
the same checkpoints yields byte-identical totals (Decimal arithmetic, rows
ordered by index). It also defines the completeness/decision contract:

- proven mismatch(es)                  -> BLOCK      (exposure is a lower bound when incomplete)
- complete scan, no mismatch           -> PASS_ELIGIBLE
- incomplete scan, no proven mismatch  -> REVIEW_REQUIRED (never PASS)
- cancelled scan                       -> impact CANCELLED (never PASS)
"""

from __future__ import annotations

import hashlib
import json
import statistics
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.engines.portfolio.consumer_protection import (
    PolicyImpactRecord,
    compute_cohort_distribution,
    compute_pipeline_impact,
)
from app.impact.config import ImpactConfig, budget_snapshot
from app.impact.models import BatchRecord, BatchState, ImpactJob, ImpactStatus
from app.impact.snapshot import APPROVED_RATING_FIELDS, PortfolioSnapshot

CENT = Decimal("0.01")


def _money(value: Decimal) -> str:
    return str(value.quantize(CENT))


class MismatchExample(BaseModel):
    row_ref: str  # opaque row reference, never a policy identifier
    expected_premium: str
    candidate_premium: str
    delta: str
    rating_inputs: dict[str, Any]


class ImpactAggregate(BaseModel):
    schema_version: str = "impact-aggregate-v1"
    tenant_id: str
    job_id: str
    mission_id: str
    status: ImpactStatus
    impact_decision: str  # BLOCK | PASS_ELIGIBLE | REVIEW_REQUIRED | CANCELLED
    completeness: str  # COMPLETE | PARTIAL | CANCELLED
    incomplete_reasons: list[str] = Field(default_factory=list)
    exposure_is_lower_bound: bool = False

    rows_total: int
    rows_in_scope: int
    processed_policies: int
    eligible_policies: int
    out_of_scope_policies: int
    out_of_scope_reasons: dict[str, int] = Field(default_factory=dict)
    successful_comparisons: int
    mismatches: int
    inconclusive: int
    inconclusive_transient: int
    unprocessed_policies: int
    coverage_pct: float
    affected_pct: float

    overcharge_count: int
    overcharge_total: str
    undercharge_count: int
    undercharge_total: str
    signed_net_delta: str
    absolute_exposure: str
    mean_abs_delta: str | None = None
    median_abs_delta: str | None = None
    mean_signed_delta: str | None = None
    median_signed_delta: str | None = None
    min_delta: str | None = None
    max_delta: str | None = None

    batch_count: int
    batches_done: int
    batches_incomplete: int
    batches_pending: int
    retry_count: int
    request_count: int
    batch_seconds_total: float
    error_classes: dict[str, int] = Field(default_factory=dict)
    breaker_opened: bool = False

    cohort_distribution: dict[str, Any] | None = None
    pipeline_impact: dict[str, Any] | None = None
    mismatch_examples: list[MismatchExample] = Field(default_factory=list)

    budget: dict[str, Any] = Field(default_factory=dict)
    budget_exhausted: list[str] = Field(default_factory=list)
    halt_reason: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    result_sha256: str = ""


def _median(values: list[Decimal]) -> Decimal:
    return Decimal(str(statistics.median(values))) if values else Decimal("0")


def aggregate_job(
    job: ImpactJob,
    batches: list[BatchRecord],
    snapshot: PortfolioSnapshot,
    config: ImpactConfig,
    declared_input_ids: set[str] | None = None,
    *,
    duration_seconds: float | None = None,
) -> ImpactAggregate:
    rows = snapshot.rows
    ordered = sorted(batches, key=lambda b: b.batch_no)

    processed = 0
    matched = 0
    out_of_scope_rows: list[list[Any]] = []
    inconclusive_rows: list[list[Any]] = []
    affected_rows: list[list[Any]] = []
    retries = requests = 0
    batch_seconds = 0.0
    errors: dict[str, int] = {}
    breaker = False
    resolved_idx: set[int] = set()
    revisions: set[str] = set()
    for b in ordered:
        retries += b.retries
        requests += b.requests
        batch_seconds += b.duration_ms / 1000.0
        breaker = breaker or b.breaker_opened
        if b.connector_revision:
            revisions.add(b.connector_revision)
        for code, n in b.error_classes.items():
            errors[code] = errors.get(code, 0) + n
        if b.completed_at is None:
            continue
        unresolved = set(b.unresolved_transient)
        resolved = [i for i in range(b.row_start, b.row_end)]
        processed += len(resolved)
        matched += b.matched
        out_of_scope_rows.extend(b.out_of_scope)
        inconclusive_rows.extend(b.inconclusive)
        affected_rows.extend(b.affected)
        resolved_idx.update(i for i in resolved if i not in unresolved)

    affected_rows.sort(key=lambda a: a[0])
    inconclusive_rows.sort(key=lambda r: r[0])
    inconclusive_transient = sum(1 for r in inconclusive_rows if r[2])
    out_of_scope = len(out_of_scope_rows)
    out_reasons: dict[str, int] = {}
    for _idx, reason in out_of_scope_rows:
        out_reasons[reason] = out_reasons.get(reason, 0) + 1
    excluded = {int(r[0]) for r in out_of_scope_rows}
    compared = matched + len(affected_rows)
    eligible_total = job.rows_total - out_of_scope
    unprocessed = job.rows_total - processed

    deltas: list[Decimal] = []
    impact_by_policy: dict[str, PolicyImpactRecord] = {}
    over_n = under_n = 0
    over_total = under_total = Decimal("0")
    for idx, expected, candidate in affected_rows:
        delta = Decimal(candidate) - Decimal(expected)
        deltas.append(delta)
        if delta > 0:
            over_n += 1
            over_total += delta
        elif delta < 0:
            under_n += 1
            under_total += -delta
        impact_by_policy[rows[idx].policy_id] = PolicyImpactRecord(
            policy_id=rows[idx].policy_id, signed_variance=delta, absolute_variance=abs(delta)
        )
    signed_net = sum(deltas, Decimal("0"))
    abs_exposure = over_total + under_total
    abs_deltas = [abs(d) for d in deltas]

    # Policies actually compared (resolved, in scope, not inconclusive).
    bad = {r[0] for r in inconclusive_rows} | excluded
    compared_policies = [rows[i] for i in sorted(resolved_idx) if i not in bad]
    cohort = compute_cohort_distribution(
        policies=compared_policies,
        impact_by_policy=impact_by_policy,
        exposed_policy_ids={p.policy_id for p in compared_policies},
    )
    pipeline = compute_pipeline_impact(
        policies=compared_policies, impact_by_policy=impact_by_policy, as_of=date.fromisoformat(job.as_of)
    )

    declared = declared_input_ids
    ranked = sorted(affected_rows, key=lambda a: (-abs(Decimal(a[2]) - Decimal(a[1])), a[0]))
    examples = [
        MismatchExample(
            row_ref=f"ROW-{idx:06d}",
            expected_premium=str(expected),
            candidate_premium=str(candidate),
            delta=str(Decimal(candidate) - Decimal(expected)),
            rating_inputs={
                k: getattr(rows[idx], k) for k in APPROVED_RATING_FIELDS if declared is None or k in declared
            },
        )
        for idx, expected, candidate in ranked[: config.max_mismatch_examples]
    ]

    done = sum(1 for b in ordered if b.state == BatchState.DONE)
    incomplete = sum(1 for b in ordered if b.state == BatchState.INCOMPLETE)
    pending = len(ordered) - done - incomplete

    reasons: list[str] = []
    if job.rows_in_scope < job.rows_total:
        reasons.append(f"ROW_BUDGET: {job.rows_total - job.rows_in_scope} rows exceed the per-mission row budget.")
    if unprocessed - (job.rows_total - job.rows_in_scope) > 0:
        reasons.append("BATCHES_NOT_COMPLETED: some batches did not finish within the mission budget.")
    if inconclusive_rows:
        reasons.append(f"INCONCLUSIVE_ROWS: {len(inconclusive_rows)} rows could not be compared.")
    reasons.extend(f"BUDGET_EXHAUSTED: {b}" for b in job.budget_exhausted)
    if job.halt_reason:
        reasons.append(f"HALTED: {job.halt_reason}")

    cancelled = job.cancel_requested
    complete = not cancelled and not reasons and unprocessed == 0 and done == len(ordered)
    if cancelled:
        status, completeness = ImpactStatus.CANCELLED, "CANCELLED"
    elif complete:
        status, completeness = ImpactStatus.COMPLETE, "COMPLETE"
    else:
        status, completeness = ImpactStatus.PARTIAL, "PARTIAL"

    mismatches = len(affected_rows)
    if mismatches > 0:
        decision = "BLOCK"
    elif cancelled:
        decision = "CANCELLED"
    elif complete:
        decision = "PASS_ELIGIBLE"
    else:
        decision = "REVIEW_REQUIRED"

    agg = ImpactAggregate(
        tenant_id=job.tenant_id, job_id=job.job_id, mission_id=job.mission_id,
        status=status, impact_decision=decision, completeness=completeness, incomplete_reasons=reasons,
        exposure_is_lower_bound=mismatches > 0 and not complete,
        rows_total=job.rows_total, rows_in_scope=job.rows_in_scope,
        processed_policies=processed, eligible_policies=eligible_total, out_of_scope_policies=out_of_scope,
        out_of_scope_reasons=dict(sorted(out_reasons.items())),
        successful_comparisons=compared, mismatches=mismatches,
        inconclusive=len(inconclusive_rows), inconclusive_transient=inconclusive_transient,
        unprocessed_policies=unprocessed,
        coverage_pct=round(compared / eligible_total * 100.0, 2) if eligible_total else 0.0,
        affected_pct=round(mismatches / eligible_total * 100.0, 2) if eligible_total else 0.0,
        overcharge_count=over_n, overcharge_total=_money(over_total),
        undercharge_count=under_n, undercharge_total=_money(under_total),
        signed_net_delta=_money(signed_net), absolute_exposure=_money(abs_exposure),
        mean_abs_delta=_money(sum(abs_deltas, Decimal("0")) / len(abs_deltas)) if deltas else None,
        median_abs_delta=_money(_median(abs_deltas)) if deltas else None,
        mean_signed_delta=_money(signed_net / len(deltas)) if deltas else None,
        median_signed_delta=_money(_median(deltas)) if deltas else None,
        min_delta=_money(min(deltas)) if deltas else None,
        max_delta=_money(max(deltas)) if deltas else None,
        batch_count=job.batch_count, batches_done=done, batches_incomplete=incomplete, batches_pending=pending,
        retry_count=retries, request_count=requests, batch_seconds_total=round(batch_seconds, 3),
        error_classes=dict(sorted(errors.items())), breaker_opened=breaker,
        cohort_distribution=cohort.model_dump(mode="json"),
        pipeline_impact=pipeline.model_dump(mode="json"),
        mismatch_examples=examples,
        budget={
            **budget_snapshot(config),
            "rows_total": job.rows_total,
            "rows_in_scope": job.rows_in_scope,
            "wall_seconds": round(duration_seconds, 3) if duration_seconds is not None else None,
        },
        budget_exhausted=list(job.budget_exhausted), halt_reason=job.halt_reason,
        provenance={
            "connector_id": job.connector.get("connector_id"),
            "engine_version": job.connector.get("engine_version"),
            "connector_revisions": sorted(revisions),
            "batch_quote_used": bool(job.connector.get("batch_max_items")),
            "source": job.source,
            "portfolio_snapshot": job.snapshot,
            "calculation_date_basis": "PORTFOLIO_POLICY_EFFECTIVE_DATE",
            "as_of": job.as_of,
            "premium_tolerance": str(config.premium_tolerance),
        },
    )
    core = agg.model_dump(mode="json", exclude={"budget", "batch_seconds_total", "retry_count", "request_count", "error_classes",
                                                  "breaker_opened", "result_sha256", "provenance"})
    agg.result_sha256 = hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return agg
