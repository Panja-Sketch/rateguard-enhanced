"""Limiter semantics — identical for the in-memory and the (real, emulator) Firestore
implementations: boundary, reset, isolation, concurrency, corruption, failure, privacy,
and cleanup."""

import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.ratelimit import (
    FirestoreRateLimiter,
    InMemoryRateLimiter,
    RateLimitConfigError,
    RateLimitPolicy,
    RateLimitUnavailable,
    resolve_policies,
)
from app.ratelimit import limiter as limiter_module
from app.ratelimit.limiter import (
    EXPIRY_GRACE_SECONDS,
    MAX_CONTENTION_RETRIES,
    METRICS,
    counter_id,
    window_bounds,
)
from app.ratelimit.policy import DEFAULT_POLICIES, parse_policy

POLICY = RateLimitPolicy("mission_create", limit=3, window_seconds=60)
T0 = 1_800_000_000.0  # a fixed instant; window starts at T0 - (T0 % 60)


@pytest.fixture(params=["memory", "firestore"])
def limiter(request):
    if request.param == "memory":
        return InMemoryRateLimiter()
    db = request.getfixturevalue("firestore_emulator_db")
    return FirestoreRateLimiter(db, collection=f"rl_{uuid.uuid4().hex[:10]}")


def _hit(limiter, tenant="t1", uid="u1", policy=POLICY, now=T0):
    return limiter.hit(tenant, uid, policy, now=now)


# ---- boundary / reset ------------------------------------------------------


def test_allows_exactly_the_limit_then_denies_with_a_bounded_retry_after(limiter):
    results = [_hit(limiter) for _ in range(POLICY.limit)]
    assert all(r.allowed for r in results)
    assert [r.remaining for r in results] == [2, 1, 0]
    denied = _hit(limiter)
    assert denied.allowed is False and denied.remaining == 0
    _, end = window_bounds(T0, POLICY)
    assert denied.retry_after_seconds == int(end - T0) or denied.retry_after_seconds == int(end - T0 + 0.999)
    assert 1 <= denied.retry_after_seconds <= POLICY.window_seconds
    # Still denied repeatedly within the window.
    assert not _hit(limiter).allowed


def test_counter_resets_at_the_next_window_boundary(limiter):
    for _ in range(POLICY.limit):
        _hit(limiter)
    assert not _hit(limiter).allowed
    _, end = window_bounds(T0, POLICY)
    assert not _hit(limiter, now=end - 0.001).allowed  # last instant of the window
    assert _hit(limiter, now=end).allowed  # first instant of the next window


def test_sequential_capacity_is_exact_and_a_window_reset_restores_all_of_it(limiter):
    policy = RateLimitPolicy("mission_create", limit=5, window_seconds=60)
    _, end = window_bounds(T0, policy)
    assert [_hit(limiter, policy=policy).allowed for _ in range(5)] == [True] * 5
    over = _hit(limiter, policy=policy)
    assert over.allowed is False and over.reason == "limit_reached" and over.retry_after_seconds >= 1
    assert [_hit(limiter, policy=policy, now=end).allowed for _ in range(5)] == [True] * 5
    assert not _hit(limiter, policy=policy, now=end).allowed


def test_retry_after_is_at_least_one_second_even_at_the_edge(limiter):
    for _ in range(POLICY.limit):
        _hit(limiter)
    _, end = window_bounds(T0, POLICY)
    assert _hit(limiter, now=end - 0.0001).retry_after_seconds >= 1


# ---- isolation -------------------------------------------------------------


def test_users_are_isolated(limiter):
    for _ in range(POLICY.limit):
        _hit(limiter, uid="u1")
    assert not _hit(limiter, uid="u1").allowed
    assert _hit(limiter, uid="u2").allowed


def test_tenants_are_isolated_even_for_the_same_uid(limiter):
    for _ in range(POLICY.limit):
        _hit(limiter, tenant="t1", uid="same")
    assert not _hit(limiter, tenant="t1", uid="same").allowed
    assert _hit(limiter, tenant="t2", uid="same").allowed


def test_operations_are_isolated(limiter):
    other = RateLimitPolicy("connector_test", limit=3, window_seconds=60)
    for _ in range(POLICY.limit):
        _hit(limiter)
    assert not _hit(limiter).allowed
    assert _hit(limiter, policy=other).allowed


def test_operation_specific_limits_are_enforced_independently(limiter):
    strict = RateLimitPolicy("connector_test", limit=1, window_seconds=60)
    loose = RateLimitPolicy("source_upload", limit=4, window_seconds=60)
    assert _hit(limiter, policy=strict).allowed and not _hit(limiter, policy=strict).allowed
    assert [_hit(limiter, policy=loose).allowed for _ in range(5)] == [True] * 4 + [False]


def test_separator_injection_cannot_merge_two_identities():
    """'a|b' + 'c' must not collide with 'a' + 'b|c' style identities."""
    p = POLICY
    assert counter_id(p, "a|b", "c", 0) != counter_id(p, "a", "b|c", 0)


# ---- concurrency -----------------------------------------------------------

CONCURRENT_LIMIT = 5
CONCURRENT_THREADS = 16
CONCURRENT_HARD_TIMEOUT = 30.0  # seconds; far above HIT_BUDGET_SECONDS, so a hang is a failure


def _run_concurrently(limiter, policy, n=CONCURRENT_THREADS, tenant="t1", uid="racer"):
    """Fires `n` simultaneous hits behind a barrier. Never blocks past the hard timeout."""
    barrier = threading.Barrier(n)
    results: list = []
    lock = threading.Lock()

    def worker():
        try:
            barrier.wait(timeout=10)
            outcome = limiter.hit(tenant, uid, policy, now=T0)
        except Exception as exc:  # a 5xx-equivalent (RateLimitUnavailable) or a bug
            outcome = exc
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(n)]
    started = time.monotonic()
    [t.start() for t in threads]
    for t in threads:
        t.join(timeout=max(0.1, CONCURRENT_HARD_TIMEOUT - (time.monotonic() - started)))
    assert not any(t.is_alive() for t in threads), "a rate-limit call hung past the hard timeout"
    return results


def test_concurrent_requests_are_safe_bounded_and_always_a_clean_result(limiter):
    """Safety, not exact capacity (that is the sequential test): never above the limit,
    at least one success on an empty window, everything else a controlled 429 with
    metadata, no 5xx-equivalent errors, bounded latency."""
    policy = RateLimitPolicy("mission_create", limit=CONCURRENT_LIMIT, window_seconds=3600)
    began = time.monotonic()
    results = _run_concurrently(limiter, policy)
    elapsed = time.monotonic() - began

    assert len(results) == CONCURRENT_THREADS
    errors = [r for r in results if isinstance(r, Exception)]
    assert not errors, f"contention must never surface as an error/503: {errors!r}"
    allowed = [r for r in results if r.allowed]
    denied = [r for r in results if not r.allowed]
    assert 1 <= len(allowed) <= CONCURRENT_LIMIT
    assert len(allowed) + len(denied) == CONCURRENT_THREADS
    for r in results:  # rate-limit metadata on every result
        assert 1 <= r.retry_after_seconds <= policy.window_seconds
        assert 0 <= r.remaining <= CONCURRENT_LIMIT
    assert all(r.remaining == 0 and not r.corrupt_state for r in denied)
    assert elapsed < CONCURRENT_HARD_TIMEOUT


def test_concurrent_slot_reservations_are_unique_so_the_limit_can_never_be_exceeded(firestore_emulator_db):
    coll = f"rl_{uuid.uuid4().hex[:10]}"
    lim = FirestoreRateLimiter(firestore_emulator_db, collection=coll)
    policy = RateLimitPolicy("mission_create", limit=CONCURRENT_LIMIT, window_seconds=3600)
    results = _run_concurrently(lim, policy, n=CONCURRENT_THREADS)
    assert not [r for r in results if isinstance(r, Exception)]
    slots = sorted(d.to_dict()["slot"] for d in firestore_emulator_db.collection(coll).stream())
    assert slots == list(range(len(slots)))  # unique, contiguous
    assert len(slots) == sum(1 for r in results if r.allowed) <= CONCURRENT_LIMIT


def test_denied_window_is_answered_by_the_fast_path_without_any_write(firestore_emulator_db):
    coll = f"rl_{uuid.uuid4().hex[:10]}"

    class NoWrites:
        """Delegates reads; any create() means the fast path was skipped."""

        def __init__(self, db):
            self._db = db

        def collection(self, name):
            real = self._db.collection(name)

            class Coll:
                def __getattr__(self, attr):
                    return getattr(real, attr)

                def document(self, doc_id):
                    ref = real.document(doc_id)
                    original = ref.create

                    def guarded(*a, **k):
                        raise AssertionError("write attempted on a full window")

                    ref.create = guarded  # type: ignore[method-assign]
                    ref._original_create = original
                    return ref

            return Coll()

    real = FirestoreRateLimiter(firestore_emulator_db, collection=coll)
    for _ in range(POLICY.limit):
        assert _hit(real).allowed
    guarded = FirestoreRateLimiter(NoWrites(firestore_emulator_db), collection=coll)
    denied = _hit(guarded)
    assert denied.allowed is False and denied.reason == "limit_reached"


# ---- contention / failure handling (Firestore-specific, fault-injected) ------


class _FaultyRef:
    """Wraps a DocumentReference; create() raises the queued exceptions first."""

    def __init__(self, real, faults):
        self._real, self._faults = real, faults

    def __getattr__(self, attr):
        return getattr(self._real, attr)

    def create(self, *a, **k):
        if self._faults:
            raise self._faults.pop(0)
        return self._real.create(*a, **k)


class _FaultyDB:
    def __init__(self, db, faults):
        self._db, self.faults = db, faults

    def collection(self, name):
        real, faults = self._db.collection(name), self.faults

        class Coll:
            def __getattr__(self, attr):
                return getattr(real, attr)

            def document(self, doc_id):
                return _FaultyRef(real.document(doc_id), faults)

        return Coll()


def _faulty_limiter(db, faults):
    return FirestoreRateLimiter(_FaultyDB(db, faults), collection=f"rl_{uuid.uuid4().hex[:10]}")


def test_retryable_transaction_conflict_is_retried_and_then_allowed(firestore_emulator_db, monkeypatch):
    from google.api_core import exceptions as gexc

    monkeypatch.setattr(time, "sleep", lambda _s: None)
    lim = _faulty_limiter(firestore_emulator_db, [gexc.Aborted("contention"), gexc.Aborted("contention")])
    d = _hit(lim)
    assert d.allowed and d.remaining == POLICY.limit - 1


def test_exhausted_contention_retries_fail_closed_as_429_not_an_error(firestore_emulator_db, monkeypatch):
    from google.api_core import exceptions as gexc

    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    before = METRICS["contention_fallback"]
    faults = [gexc.Aborted("contention") for _ in range(50)]
    lim = _faulty_limiter(firestore_emulator_db, faults)
    d = _hit(lim)
    assert d.allowed is False and d.contention and not d.corrupt_state
    assert 1 <= d.retry_after_seconds <= POLICY.window_seconds
    assert len(sleeps) == MAX_CONTENTION_RETRIES  # bounded retries, jittered, then stop
    assert all(0 < s <= 0.05 * MAX_CONTENTION_RETRIES for s in sleeps)
    assert len(faults) == 50 - (MAX_CONTENTION_RETRIES + 1)  # no unbounded retry loop
    assert METRICS["contention_fallback"] == before + 1


@pytest.mark.parametrize("error_name", ["ServiceUnavailable", "DeadlineExceeded", "InternalServerError", "PermissionDenied"])
def test_genuine_storage_outage_raises_rate_limit_unavailable(firestore_emulator_db, error_name):
    from google.api_core import exceptions as gexc

    before = METRICS["storage_unavailable"]
    lim = _faulty_limiter(firestore_emulator_db, [getattr(gexc, error_name)("down: secret-project-name")])
    with pytest.raises(RateLimitUnavailable) as exc:
        _hit(lim)
    assert "secret-project-name" not in str(exc.value)
    assert METRICS["storage_unavailable"] == before + 1


def test_probe_budget_exhaustion_fails_closed_as_contention(firestore_emulator_db, monkeypatch):
    from google.api_core import exceptions as gexc

    monkeypatch.setattr(limiter_module, "MAX_SLOT_PROBES", 2)
    lim = _faulty_limiter(firestore_emulator_db, [gexc.AlreadyExists("x")] * 10)
    d = _hit(lim, policy=RateLimitPolicy("mission_create", limit=50, window_seconds=60))
    assert d.allowed is False and d.contention


def test_time_budget_exhaustion_fails_closed_as_429_within_the_bound(firestore_emulator_db, monkeypatch):
    monkeypatch.setattr(limiter_module, "HIT_BUDGET_SECONDS", 0.0)
    began = time.monotonic()
    d = _hit(FirestoreRateLimiter(firestore_emulator_db, collection=f"rl_{uuid.uuid4().hex[:10]}"))
    assert d.allowed is False and d.contention
    assert time.monotonic() - began < 1.0


# ---- corruption / failure (Firestore-specific) -----------------------------


def test_corrupt_counter_state_fails_closed_for_that_window_only(firestore_emulator_db):
    coll = f"rl_{uuid.uuid4().hex[:10]}"
    lim = FirestoreRateLimiter(firestore_emulator_db, collection=coll)
    start, end = window_bounds(T0, POLICY)
    cid = counter_id(POLICY, "t1", "u1", start)
    doc = firestore_emulator_db.collection(coll).document(f"{cid}_0")
    good = {
        "slot": 0, "window_key": cid, "window_start": start,
        "operation": POLICY.operation, "tenant_id": "t1", "uid": "u1",
    }
    before = METRICS["corrupt_state"]
    # A malformed document sitting on the slot being claimed (not matched by the
    # window_key count) denies the request instead of being trusted.
    bads = (
        {**good, "slot": "zero", "window_key": "x"},
        {**good, "slot": True, "window_key": None},
        {**good, "slot": 7, "window_key": 5},
        {"window_start": start},  # missing everything
        {**good, "window_start": start + 1, "window_key": "other"},
        {**good, "tenant_id": "someone-else", "window_key": "other"},
        {**good, "uid": "someone-else", "window_key": "other"},
        {**good, "operation": "connector_test", "window_key": "other"},
    )
    for bad in bads:
        doc.set(bad)
        d = lim.hit("t1", "u1", POLICY, now=T0)
        assert d.allowed is False and d.corrupt_state is True
        assert 1 <= d.retry_after_seconds <= POLICY.window_seconds
    assert METRICS["corrupt_state"] == before + len(bads)
    # The next window uses fresh documents and recovers automatically.
    assert lim.hit("t1", "u1", POLICY, now=end).allowed


def test_corrupt_documents_that_count_as_used_can_never_cause_over_admission(firestore_emulator_db):
    coll = f"rl_{uuid.uuid4().hex[:10]}"
    lim = FirestoreRateLimiter(firestore_emulator_db, collection=coll)
    start, _ = window_bounds(T0, POLICY)
    cid = counter_id(POLICY, "t1", "u1", start)
    for n in range(POLICY.limit):
        firestore_emulator_db.collection(coll).document(f"{cid}_{n}").set({"window_key": cid, "slot": "junk"})
    d = lim.hit("t1", "u1", POLICY, now=T0)
    assert d.allowed is False and d.reason == "limit_reached"


def test_storage_failure_raises_rate_limit_unavailable_not_a_silent_allow():
    class Boom:
        def collection(self, *_):
            raise RuntimeError("firestore down: secret-project-name")

    with pytest.raises(RateLimitUnavailable) as exc:
        FirestoreRateLimiter(Boom()).hit("t1", "u1", POLICY, now=T0)
    assert "secret-project-name" not in str(exc.value)


def test_events_are_logged_without_identifiers(firestore_emulator_db, caplog):
    lim = FirestoreRateLimiter(firestore_emulator_db, collection=f"rl_{uuid.uuid4().hex[:10]}")
    with caplog.at_level("INFO", logger="app.ratelimit.limiter"):
        for _ in range(POLICY.limit + 1):
            lim.hit("tenant-secret", "uid-secret", POLICY, now=T0)
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "event=allowed" in text and "event=limit_reached" in text
    assert "tenant-secret" not in text and "uid-secret" not in text


# ---- privacy ---------------------------------------------------------------


def test_counter_documents_contain_no_tokens_ips_or_payloads(firestore_emulator_db):
    coll = f"rl_{uuid.uuid4().hex[:10]}"
    lim = FirestoreRateLimiter(firestore_emulator_db, collection=coll)
    lim.hit("tenant-x", "uid-y", POLICY, now=T0)
    docs = list(firestore_emulator_db.collection(coll).stream())
    assert len(docs) == 1
    data = docs[0].to_dict()
    assert set(data) == {"tenant_id", "uid", "operation", "window_start", "window_key", "slot", "expires_at"}
    assert "uid-y" not in docs[0].id and "tenant-x" not in docs[0].id  # id is a hash + slot number
    assert docs[0].id == f"{data['window_key']}_0" and len(data["window_key"]) == 40


# ---- expiry / cleanup ------------------------------------------------------


def test_expired_counters_are_purged_and_live_ones_kept(firestore_emulator_db):
    coll = f"rl_{uuid.uuid4().hex[:10]}"
    lim = FirestoreRateLimiter(firestore_emulator_db, collection=coll)
    old_start, old_end = window_bounds(T0, POLICY)
    lim.hit("t1", "old", POLICY, now=T0)
    later = old_end + EXPIRY_GRACE_SECONDS + 120
    lim.hit("t1", "new", POLICY, now=later)

    docs = {d.to_dict()["uid"]: d.to_dict() for d in firestore_emulator_db.collection(coll).stream()}
    assert docs["old"]["expires_at"] == FirestoreRateLimiter.ttl_cutoff(old_end)
    assert docs["old"]["expires_at"] < datetime.fromtimestamp(later, tz=UTC)
    assert docs["new"]["expires_at"] > datetime.fromtimestamp(later, tz=UTC) + timedelta(seconds=EXPIRY_GRACE_SECONDS - 1)

    assert lim.purge_expired(now=later) == 1
    remaining = [d.to_dict()["uid"] for d in firestore_emulator_db.collection(coll).stream()]
    assert remaining == ["new"]
    assert lim.purge_expired(now=later) == 0


def test_memory_limiter_drops_finished_windows():
    lim = InMemoryRateLimiter()
    lim.hit("t1", "u1", POLICY, now=T0)
    assert len(lim._counters) == 1
    lim.hit("t1", "u2", POLICY, now=T0 + 10 * POLICY.window_seconds)
    assert len(lim._counters) == 1  # the old window was cleaned up


# ---- policies --------------------------------------------------------------


def test_defaults_cover_every_required_action_and_connector_test_and_mission_are_strictest():
    assert set(DEFAULT_POLICIES) == {
        "source_upload", "source_compile", "mission_create", "connector_test",
        "explanation_create", "evidence_download", "source_download",
    }
    lim = {op: v[0] for op, v in DEFAULT_POLICIES.items()}
    assert lim["connector_test"] < lim["mission_create"] < min(
        lim[o] for o in ("source_upload", "source_compile", "explanation_create", "evidence_download", "source_download")
    )


@pytest.mark.parametrize("raw", ["", "5", "0/60", "5/0", "-1/60", "5/-1", "x/y", "5/60/1", "10001/60", "5/86401", "1.5/60"])
def test_invalid_policy_strings_are_rejected(raw):
    with pytest.raises(RateLimitConfigError):
        parse_policy("mission_create", raw)


def test_overrides_are_applied_and_unknown_operations_rejected():
    p = resolve_policies({"mission_create": "2/30"})
    assert (p["mission_create"].limit, p["mission_create"].window_seconds) == (2, 30)
    assert p["connector_test"].limit == DEFAULT_POLICIES["connector_test"][0]
    with pytest.raises(RateLimitConfigError):
        resolve_policies({"delete_everything": "1/1"})
