"""Limiter semantics — identical for the in-memory and the (real, emulator) Firestore
implementations: boundary, reset, isolation, concurrency, corruption, failure, privacy,
and cleanup."""

import random
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
from app.ratelimit.limiter import EXPIRY_GRACE_SECONDS, counter_id, window_bounds
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


def test_separator_injection_cannot_merge_two_identities():
    """'a|b' + 'c' must not collide with 'a' + 'b|c' style identities."""
    p = POLICY
    assert counter_id(p, "a|b", "c", 0) != counter_id(p, "a", "b|c", 0)


# ---- concurrency -----------------------------------------------------------


def test_concurrent_requests_never_exceed_the_limit(limiter):
    policy = RateLimitPolicy("mission_create", limit=5, window_seconds=3600)
    outcomes: list[bool] = []
    lock = threading.Lock()

    def worker():
        for _ in range(40):  # a client retrying (with jitter) on transient contention errors
            try:
                d = limiter.hit("t1", "racer", policy, now=T0)
            except RateLimitUnavailable:
                time.sleep(random.uniform(0.01, 0.15))
                continue
            with lock:
                outcomes.append(d.allowed)
            return

    threads = [threading.Thread(target=worker) for _ in range(16)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(outcomes) == 5, f"exactly the limit must be allowed, got {sum(outcomes)} of {len(outcomes)}"
    assert len(outcomes) == 16


# ---- corruption / failure (Firestore-specific) -----------------------------


def test_corrupt_counter_state_fails_closed_for_that_window_only(firestore_emulator_db):
    coll = f"rl_{uuid.uuid4().hex[:10]}"
    lim = FirestoreRateLimiter(firestore_emulator_db, collection=coll)
    start, end = window_bounds(T0, POLICY)
    doc = firestore_emulator_db.collection(coll).document(counter_id(POLICY, "t1", "u1", start))
    for bad in (
        {"count": "lots", "window_start": start, "operation": POLICY.operation, "tenant_id": "t1", "uid": "u1"},
        {"count": -5, "window_start": start, "operation": POLICY.operation, "tenant_id": "t1", "uid": "u1"},
        {"count": True, "window_start": start, "operation": POLICY.operation, "tenant_id": "t1", "uid": "u1"},
        {"window_start": start},  # missing everything
        {"count": 0, "window_start": start + 1, "operation": POLICY.operation, "tenant_id": "t1", "uid": "u1"},
        {"count": 0, "window_start": start, "operation": POLICY.operation, "tenant_id": "someone-else", "uid": "u1"},
    ):
        doc.set(bad)
        d = lim.hit("t1", "u1", POLICY, now=T0)
        assert d.allowed is False and d.corrupt_state is True
        assert 1 <= d.retry_after_seconds <= POLICY.window_seconds
    # The next window uses a fresh document and recovers automatically.
    assert lim.hit("t1", "u1", POLICY, now=end).allowed


def test_storage_failure_raises_rate_limit_unavailable_not_a_silent_allow():
    class Boom:
        def collection(self, *_):
            raise RuntimeError("firestore down: secret-project-name")

        def transaction(self):
            raise RuntimeError("firestore down: secret-project-name")

    with pytest.raises(RateLimitUnavailable) as exc:
        FirestoreRateLimiter(Boom()).hit("t1", "u1", POLICY, now=T0)
    assert "secret-project-name" not in str(exc.value)


# ---- privacy ---------------------------------------------------------------


def test_counter_documents_contain_no_tokens_ips_or_payloads(firestore_emulator_db):
    coll = f"rl_{uuid.uuid4().hex[:10]}"
    lim = FirestoreRateLimiter(firestore_emulator_db, collection=coll)
    lim.hit("tenant-x", "uid-y", POLICY, now=T0)
    docs = list(firestore_emulator_db.collection(coll).stream())
    assert len(docs) == 1
    data = docs[0].to_dict()
    assert set(data) == {"tenant_id", "uid", "operation", "window_start", "count", "expires_at"}
    assert "uid-y" not in docs[0].id and "tenant-x" not in docs[0].id  # id is a hash
    assert len(docs[0].id) == 40


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
