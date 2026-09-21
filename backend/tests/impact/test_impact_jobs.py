"""Connector-backed impact: correctness, completion semantics, idempotency,
resilience, tenancy, budgets and determinism."""

from __future__ import annotations

import pytest

from app.core.runtime_config import RuntimeConfigError
from app.impact.aggregate import aggregate_job
from app.impact.config import resolve_impact_config
from app.impact.models import BatchState, ImpactStatus, LeaseResult
from app.impact.processor import BatchOutcome
from app.impact.resilience import CircuitBreaker
from tests.impact.conftest import fast_config, make_harness, status_app


def _run(snapshot, package, version="canonical-v1", **kw):
    h = make_harness(snapshot, package, **kw.pop("harness", {}))
    job, snap = h.plan(snapshot, package, version=version, **kw)
    agg = h.run(job, snap, package)
    h.dispatcher.close()
    return h, job, agg


def _reconciles(a):
    return a.successful_comparisons + a.inconclusive + a.out_of_scope_policies + a.unprocessed_policies == a.rows_total


@pytest.mark.parametrize("batch", [True, False])
def test_clean_connector_is_complete_pass_eligible(snapshot, package, batch):
    _, _, a = _run(snapshot, package, harness={"batch": batch})
    assert a.status == ImpactStatus.COMPLETE and a.impact_decision == "PASS_ELIGIBLE"
    assert a.mismatches == 0 and a.inconclusive == 0 and a.coverage_pct == 100.0
    assert a.out_of_scope_reasons.get("OUTSIDE_EFFECTIVE_PERIOD", 0) == a.out_of_scope_policies > 0
    assert _reconciles(a)


def test_defective_connector_blocks_with_exact_exposure(snapshot, package):
    _, _, a = _run(snapshot, package, version="defective-v1")
    assert a.impact_decision == "BLOCK" and a.status == ImpactStatus.COMPLETE
    assert a.mismatches > 0 and a.undercharge_count == a.mismatches and a.overcharge_count == 0
    assert a.absolute_exposure == a.undercharge_total and a.min_delta == a.max_delta == "-45.00"
    assert not a.exposure_is_lower_bound and a.mismatch_examples and _reconciles(a)
    assert all(ex.row_ref.startswith("ROW-") for ex in a.mismatch_examples)
    assert not any("policy_id" in ex.rating_inputs for ex in a.mismatch_examples)


def test_single_and_batch_paths_agree(snapshot, package):
    _, _, b = _run(snapshot, package, version="defective-v1", harness={"batch": True})
    _, _, s = _run(snapshot, package, version="defective-v1", harness={"batch": False})
    assert b.result_sha256 == s.result_sha256


def test_aggregation_is_repeatable(snapshot, package):
    h, job, a = _run(snapshot, package, version="defective-v1")
    again = aggregate_job(job, h.store.list_batches(job.tenant_id, job.job_id), snapshot, h.config,
                          {i.id for i in package.inputs})
    assert again.result_sha256 == a.result_sha256
    assert again.model_dump(exclude={"budget"}) == a.model_dump(exclude={"budget"})


@pytest.mark.parametrize("batch", [True, False])
def test_partial_outage_without_mismatch_requires_review(snapshot, package, monkeypatch, batch):
    monkeypatch.setenv("RATING_ENGINE_FAULT_MODE", "subset503:0.3")
    _, _, a = _run(snapshot, package, harness={"batch": batch, "config": fast_config(max_batch_attempts=2)})
    assert a.status == ImpactStatus.PARTIAL and a.impact_decision == "REVIEW_REQUIRED"
    assert a.mismatches == 0 and a.inconclusive > 0 and a.inconclusive_transient == a.inconclusive
    assert 0 < a.coverage_pct < 100 and _reconciles(a)
    assert a.retry_count >= 0 and not a.exposure_is_lower_bound


def test_partial_outage_with_proven_mismatch_blocks_with_lower_bound(snapshot, package, monkeypatch):
    monkeypatch.setenv("RATING_ENGINE_FAULT_MODE", "subset503:0.3")
    _, _, a = _run(snapshot, package, version="defective-v1",
                   harness={"config": fast_config(max_batch_attempts=2)})
    assert a.impact_decision == "BLOCK" and a.completeness == "PARTIAL" and a.exposure_is_lower_bound
    assert a.mismatches > 0 and _reconciles(a)


def test_recovery_resumes_only_unresolved_rows_without_double_counting(snapshot, package, monkeypatch):
    monkeypatch.setenv("RATING_ENGINE_FAULT_MODE", "subset503:0.3")
    h = make_harness(snapshot, package)
    job, snap = h.plan(snapshot, package, version="defective-v1")
    h.store.ensure_batches(job.tenant_id, job.job_id, [(0, 0, 50)])
    assert h.processor.process(job.tenant_id, job.job_id, 0, "w1") == BatchOutcome.INCOMPLETE
    first = h.store.get_batch(job.tenant_id, job.job_id, 0)
    assert first.state == BatchState.INCOMPLETE and first.unresolved_transient
    monkeypatch.delenv("RATING_ENGINE_FAULT_MODE")  # connector recovers
    assert h.processor.process(job.tenant_id, job.job_id, 0, "w2") == BatchOutcome.DONE
    done = h.store.get_batch(job.tenant_id, job.job_id, 0)
    resolved = done.matched + len(done.affected) + len(done.out_of_scope) + len(done.inconclusive)
    assert resolved == 50 and not done.inconclusive
    assert len(first.affected) <= len(done.affected)  # retained results kept, never recounted
    assert done.attempts == 2
    h.dispatcher.close()


def test_auth_failure_is_permanent_never_retried_and_needs_review(snapshot, package):
    calls: list[int] = []
    _, _, a = _run(snapshot, package, harness={"app": status_app(401, calls), "batch": False})
    assert a.impact_decision == "REVIEW_REQUIRED" and a.status == ImpactStatus.PARTIAL
    assert a.mismatches == 0 and a.inconclusive_transient == 0
    assert set(a.error_classes) == {"CONNECTOR_AUTH_DENIED"} and a.breaker_opened
    assert a.halt_reason == "CONNECTOR_PERMANENT_FAILURE" or a.batches_done == a.batch_count
    assert len(calls) < a.eligible_policies  # breaker stopped hammering the target
    assert a.retry_count == 0


def test_permanent_4xx_is_not_retried(snapshot, package):
    calls: list[int] = []
    h = make_harness(snapshot, package, app=status_app(400, calls), batch=False)
    job, _ = h.plan(snapshot, package)
    h.store.ensure_batches(job.tenant_id, job.job_id, [(0, 0, 10)])
    h.processor.process(job.tenant_id, job.job_id, 0, "w")
    b = h.store.get_batch(job.tenant_id, job.job_id, 0)
    assert b.retries == 0 and b.state == BatchState.DONE
    assert all(not r[2] for r in b.inconclusive)
    h.dispatcher.close()


def test_429_is_retried_and_recovers(snapshot, package):
    state = {"n": 0}
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    from rating_engine.main import app as engine

    async def flaky(request):
        state["n"] += 1
        if state["n"] % 2 == 1:
            return JSONResponse({}, status_code=429)
        import httpx
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=engine), base_url="http://e") as c:
            r = await c.post("/quote", content=await request.body(), headers={"content-type": "application/json"})
        return JSONResponse(r.json(), status_code=r.status_code)

    app = Starlette(routes=[Route("/quote", flaky, methods=["POST"])])
    h = make_harness(snapshot, package, app=app, batch=False)
    job, snap = h.plan(snapshot, package)
    h.store.ensure_batches(job.tenant_id, job.job_id, [(0, 0, 30)])
    assert h.processor.process(job.tenant_id, job.job_id, 0, "w") == BatchOutcome.DONE
    b = h.store.get_batch(job.tenant_id, job.job_id, 0)
    assert b.retries > 0 and not b.inconclusive
    h.dispatcher.close()


def test_duplicate_and_redelivered_batches_do_not_double_count(snapshot, package):
    h = make_harness(snapshot, package)
    job, snap = h.plan(snapshot, package, version="defective-v1")
    a1 = h.run(job, snap, package)
    batches = h.store.list_batches(job.tenant_id, job.job_id)
    for b in batches[:5]:  # duplicate Pub/Sub deliveries of finished batches
        assert h.processor.process(job.tenant_id, job.job_id, b.batch_no, "dup") in (BatchOutcome.DUPLICATE, BatchOutcome.CLOSED)
    again = aggregate_job(job, h.store.list_batches(job.tenant_id, job.job_id), snap, h.config, {i.id for i in package.inputs})
    assert again.mismatches == a1.mismatches and again.result_sha256 == a1.result_sha256
    h.dispatcher.close()


def test_live_lease_blocks_concurrent_delivery_and_fences_stale_writer(snapshot, package):
    h = make_harness(snapshot, package)
    job, _ = h.plan(snapshot, package)
    h.store.ensure_batches(job.tenant_id, job.job_id, [(0, 0, 10)])
    res, _ = h.store.lease_batch(job.tenant_id, job.job_id, 0, "A", 60, 3)
    assert res == LeaseResult.ACQUIRED
    assert h.processor.process(job.tenant_id, job.job_id, 0, "B") == BatchOutcome.DUPLICATE
    assert h.store.complete_batch(job.tenant_id, job.job_id, 0, "B", {"matched": 99}, BatchState.DONE) is False
    assert h.store.get_batch(job.tenant_id, job.job_id, 0).matched == 0
    h.dispatcher.close()


def test_worker_restart_resumes_same_job_without_recomputing_done_batches(snapshot, package):
    processed: list[int] = []

    def wrap(fn):
        def inner(t, j, n, o):
            out = fn(t, j, n, o)
            if out == BatchOutcome.DONE:
                processed.append(n)
            return out
        return inner

    store = make_harness(snapshot, package).store
    h1 = make_harness(snapshot, package, store=store, dispatch_wrapper=wrap,
                      config=fast_config(max_mission_seconds=31, batch_timeout_seconds=30))
    job, snap = h1.plan(snapshot, package, version="defective-v1")
    # First coordinator "dies" early: run only a couple of poll rounds by cancelling via deadline hook.
    rounds = {"n": 0}

    def die():
        rounds["n"] += 1
        return rounds["n"] > 4

    h1.coordinator.run(job, snap, declared_input_ids=None, cancellation_check=die, heartbeat=lambda: None)
    h1.dispatcher.close()
    # A cancel flag was recorded by that abort; emulate a fresh coordinator resuming after redelivery.
    store.update_job(job.tenant_id, job.job_id, {"cancel_requested": False, "finalized_at": None,
                                                 "aggregate": None, "status": ImpactStatus.RUNNING})
    done_before = {b.batch_no for b in store.list_batches(job.tenant_id, job.job_id) if b.state == BatchState.DONE}
    h2 = make_harness(snapshot, package, store=store, dispatch_wrapper=wrap)
    job2, snap2 = h2.plan(snapshot, package, version="defective-v1")
    assert job2.job_id == job.job_id
    processed.clear()
    a = h2.run(job2, snap2, package)
    h2.dispatcher.close()
    assert a.status == ImpactStatus.COMPLETE and not (set(processed) & done_before)
    _, _, fresh = _run(snapshot, package, version="defective-v1")
    assert a.result_sha256 == fresh.result_sha256


def test_cancellation_yields_cancelled_never_pass(snapshot, package):
    h = make_harness(snapshot, package)
    job, snap = h.plan(snapshot, package)
    a = h.run(job, snap, package, cancel=lambda: True)
    h.dispatcher.close()
    assert a.status == ImpactStatus.CANCELLED and a.impact_decision == "CANCELLED"
    assert a.completeness == "CANCELLED"


def test_cross_tenant_job_is_indistinguishable_from_missing(snapshot, package):
    h = make_harness(snapshot, package)
    job, _ = h.plan(snapshot, package, tenant="tenant-a")
    assert h.store.get_job("tenant-b", job.job_id) is None
    assert h.store.find_jobs_for_mission("tenant-b", job.mission_id) == []
    assert h.store.list_batches("tenant-b", job.job_id) == []
    assert h.store.lease_batch("tenant-b", job.job_id, 0, "x", 60, 3)[0] == LeaseResult.NOT_FOUND
    other, _ = h.plan(snapshot, package, tenant="tenant-b")
    assert other.job_id != job.job_id  # tenant is part of the idempotency identity
    h.dispatcher.close()


def test_row_budget_exhaustion_is_partial_review(snapshot, package):
    h = make_harness(snapshot, package, config=fast_config(max_policies=100))
    job, snap = h.plan(snapshot, package)
    assert job.rows_in_scope == 100 < job.rows_total
    a = h.run(job, snap, package)
    h.dispatcher.close()
    assert a.status == ImpactStatus.PARTIAL and a.impact_decision == "REVIEW_REQUIRED"
    assert "ROW_BUDGET" in a.budget_exhausted and a.unprocessed_policies >= 500 and _reconciles(a)


def test_time_budget_exhaustion_is_partial_review(snapshot, package):
    import time

    def slow(fn):
        def inner(t, j, n, o):
            time.sleep(0.05)
            return fn(t, j, n, o)
        return inner

    cfg = fast_config(max_mission_seconds=31, batch_timeout_seconds=30)
    h = make_harness(snapshot, package, config=cfg, dispatch_wrapper=slow)
    job, snap = h.plan(snapshot, package)
    clock = iter([0.0, 0.0] + [100.0] * 10_000)
    h.coordinator._clock = lambda: next(clock)  # deadline passes immediately after the first poll
    a = h.run(job, snap, package)
    h.dispatcher.close()
    assert a.status == ImpactStatus.PARTIAL and "MAX_MISSION_SECONDS" in a.budget_exhausted
    assert a.impact_decision == "REVIEW_REQUIRED"


def test_finalize_is_first_writer_wins(snapshot, package):
    h = make_harness(snapshot, package)
    job, snap = h.plan(snapshot, package)
    first = h.run(job, snap, package)
    _, created = h.store.finalize_job(job.tenant_id, job.job_id, ImpactStatus.FAILED, {"x": 1})
    assert created is False
    again = h.coordinator.finalize(job.job_id, job.tenant_id, snap, None)
    assert again.result_sha256 == first.result_sha256
    h.dispatcher.close()


def test_circuit_breaker_opens_then_half_opens():
    t = {"now": 0.0}
    cb = CircuitBreaker(3, 10.0, clock=lambda: t["now"])
    for _ in range(3):
        cb.record_failure("CONNECTOR_TIMEOUT")
    assert not cb.allow()
    t["now"] = 11.0
    assert cb.allow()
    cb.record_failure("CONNECTOR_TIMEOUT")
    assert not cb.allow()
    cb2 = CircuitBreaker(3, 10.0)
    cb2.record_failure("CONNECTOR_AUTH_DENIED")
    assert cb2.permanent_code == "CONNECTOR_AUTH_DENIED" and not cb2.allow()


def test_config_validation_rejects_unsafe_values():
    ok = resolve_impact_config({})
    assert ok.batch_size == 200 and ok.global_request_concurrency <= 20
    for env in (
        {"RATEGUARD_IMPACT_BATCH_SIZE": "5000"},
        {"RATEGUARD_IMPACT_MAX_INFLIGHT_BATCHES": "10", "RATEGUARD_IMPACT_REQUEST_CONCURRENCY_PER_BATCH": "10"},
        {"RATEGUARD_IMPACT_MAX_QPS": "abc"},
        {"RATEGUARD_IMPACT_BATCH_TIMEOUT_SECONDS": "500", "RATEGUARD_IMPACT_MAX_MISSION_SECONDS": "100"},
        {"RATEGUARD_IMPACT_PREMIUM_TOLERANCE": "50"},
    ):
        with pytest.raises(RuntimeConfigError):
            resolve_impact_config(env)


def test_evidence_size_budget_trims_examples_but_not_totals(snapshot, package):
    h = make_harness(snapshot, package, config=fast_config(max_evidence_bytes=10_000))
    job, snap = h.plan(snapshot, package, version="defective-v1")
    a = h.run(job, snap, package)
    h.dispatcher.close()
    full = make_harness(snapshot, package)
    job2, snap2 = full.plan(snapshot, package, version="defective-v1", mission="MIS-T2")
    b = full.run(job2, snap2, package)
    full.dispatcher.close()
    assert a.budget["evidence_bytes"] <= 10_000 and "mismatch_examples" in a.budget["evidence_trimmed"]
    assert a.mismatches == b.mismatches and a.absolute_exposure == b.absolute_exposure
