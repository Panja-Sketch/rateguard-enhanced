"""Rate limiters.

`FirestoreRateLimiter` is the Cloud Run implementation: a token-slot
fixed-window limiter shared by every instance, so limits hold no matter how
many API instances are running (a process-local counter would multiply the
limit by the instance count). `InMemoryRateLimiter` has the same semantics for
local development and tests only.

Privacy: counters are keyed by tenant id + authenticated uid + operation.
They never contain bearer tokens, IP addresses or request payloads, and the
document id is a hash so it carries no identifier at all.

Cleanup: every counter document has an `expires_at` field. Enable the Firestore
TTL policy on it (see infrastructure/firestore.indexes.json and
docs/security/AUTHORIZATION_MATRIX.md); `purge_expired()` is the manual
equivalent used by tests/maintenance.

Failure behaviour (fail closed): a slot document that is corrupt (wrong types or
mismatched identity/window) denies the action for the rest of that window (the
next window uses fresh documents); contention / retry exhaustion denies with a
429; only a genuine storage failure raises `RateLimitUnavailable`, which the API
turns into 503 for these expensive actions.
"""

import hashlib
import json
import logging
import math
import random
import threading
import time
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.ratelimit.policy import RateLimitPolicy

logger = logging.getLogger(__name__)

COLLECTION = "rate_limits"
# Documents outlive their window by this long before TTL/purge removes them.
EXPIRY_GRACE_SECONDS = 3600
# Hard bounds so one request can never hang or loop (then it fails closed as 429).
HIT_BUDGET_SECONDS = 5.0  # total wall-clock budget for one hit()
RPC_TIMEOUT_SECONDS = 2.0  # per Firestore call
MAX_SLOT_PROBES = 32  # slots tried before giving up under extreme contention
MAX_CONTENTION_RETRIES = 3  # jittered retries of retryable (Aborted) conflicts
CONTENTION_RETRY_AFTER = 1  # seconds advertised when denied by contention

# In-process counters mirroring the structured log events.
METRICS: Counter[str] = Counter()
_EVENT_NAMES = {
    "": "allowed",
    "limit_reached": "limit_reached",
    "contention": "contention_fallback",
    "corrupt_state": "corrupt_state",
    "storage_unavailable": "storage_unavailable",
}


class _BudgetExhausted(Exception):
    pass


def _emit(kind: str, operation: str, **fields: str) -> None:
    """Structured event: operation + outcome only - never tokens, uids, IPs or payloads."""
    event = _EVENT_NAMES[kind]
    METRICS[event] += 1
    level = logging.WARNING if kind in ("contention", "corrupt_state", "storage_unavailable") else logging.INFO
    detail = "".join(f" {k}={v}" for k, v in fields.items())
    logger.log(
        level,
        "RATE_LIMIT_EVENT event=%s operation=%s%s",
        event,
        operation,
        detail,
        extra={"rate_limit_event": event, "rate_limit_operation": operation},
    )


class RateLimitUnavailable(RuntimeError):
    """The shared counter could not be read/updated; callers fail closed."""


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: int
    corrupt_state: bool = False
    reason: str = ""  # "", "limit_reached", "contention" or "corrupt_state"

    @property
    def contention(self) -> bool:
        return self.reason == "contention"


def window_bounds(now: float, policy: RateLimitPolicy) -> tuple[int, int]:
    start = int(now // policy.window_seconds) * policy.window_seconds
    return start, start + policy.window_seconds


def _retry_after(now: float, window_end: int) -> int:
    return max(1, int(math.ceil(window_end - now)))


def counter_id(policy: RateLimitPolicy, tenant_id: str, uid: str, window_start: int) -> str:
    # JSON encoding is unambiguous: no separator inside a uid/tenant can make two
    # different identities hash to the same counter.
    raw = json.dumps([policy.operation, tenant_id, uid, window_start])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


class RateLimiter(ABC):
    @abstractmethod
    def hit(self, tenant_id: str, uid: str, policy: RateLimitPolicy, now: float | None = None) -> RateDecision:
        """Counts one attempt and returns whether it is allowed."""


class InMemoryRateLimiter(RateLimiter):
    """Process-local (development/tests only)."""

    def __init__(self) -> None:
        self._counters: dict[str, tuple[int, int]] = {}  # id -> (window_start, count)
        self._lock = threading.Lock()

    def hit(self, tenant_id: str, uid: str, policy: RateLimitPolicy, now: float | None = None) -> RateDecision:
        now = time.time() if now is None else now
        start, end = window_bounds(now, policy)
        cid = counter_id(policy, tenant_id, uid, start)
        with self._lock:
            # Opportunistic cleanup of finished windows.
            for key in [k for k, (ws, _) in self._counters.items() if ws + 2 * policy.window_seconds < now]:
                del self._counters[key]
            _, count = self._counters.get(cid, (start, 0))
            if count >= policy.limit:
                return RateDecision(False, 0, _retry_after(now, end), reason="limit_reached")
            self._counters[cid] = (start, count + 1)
            return RateDecision(True, policy.limit - count - 1, _retry_after(now, end))


class FirestoreRateLimiter(RateLimiter):
    """Token-slot fixed window: each window owns `limit` slot documents
    (`<counter_id>_<n>`); a request is allowed only by *creating* an unused slot.

    `create()` is atomic and fails with AlreadyExists, so the allowed count can
    never exceed the limit no matter how many instances race. There is no
    transaction and no hot counter document, hence no lock storm: concurrent
    requests probe successive slots (bounded). If the probe budget, the retry
    budget or the time budget runs out the request fails closed as a 429
    (`contention`): it never over-admits, hangs, or turns contention into a 5xx.
    Only a genuine service failure raises `RateLimitUnavailable` (503).
    """

    def __init__(self, db: Any, collection: str = COLLECTION) -> None:
        self._db = db
        self._collection = collection

    def hit(self, tenant_id: str, uid: str, policy: RateLimitPolicy, now: float | None = None) -> RateDecision:
        from google.api_core import exceptions as gexc
        from google.cloud.firestore_v1.base_query import FieldFilter

        wall_now = time.time() if now is None else now
        start, end = window_bounds(wall_now, policy)
        cid = counter_id(policy, tenant_id, uid, start)
        retry_after = _retry_after(wall_now, end)
        contention_retry_after = min(retry_after, CONTENTION_RETRY_AFTER)
        expires_at = datetime.fromtimestamp(end + EXPIRY_GRACE_SECONDS, tz=UTC)
        deadline = time.monotonic() + HIT_BUDGET_SECONDS

        def budget() -> float:
            left = deadline - time.monotonic()
            if left <= 0:
                raise _BudgetExhausted
            return min(left, RPC_TIMEOUT_SECONDS)

        def valid(data: dict[str, Any] | None, slot: int) -> bool:
            data = data or {}
            n = data.get("slot")
            return (
                isinstance(n, int)
                and not isinstance(n, bool)
                and n == slot
                and data.get("window_key") == cid
                and data.get("window_start") == start
                and data.get("operation") == policy.operation
                and data.get("tenant_id") == tenant_id
                and data.get("uid") == uid
            )

        def decide() -> RateDecision:
            coll = self._db.collection(self._collection)
            # Fast path: a window already at/over the limit is denied by one read.
            query = coll.where(filter=FieldFilter("window_key", "==", cid))
            used = int(query.count().get(retry=None, timeout=budget())[0][0].value)
            if used >= policy.limit:
                return RateDecision(False, 0, retry_after, reason="limit_reached")
            slot, probes, conflicts = max(used, 0), 0, 0
            while slot < policy.limit:
                if probes >= MAX_SLOT_PROBES:
                    return RateDecision(False, 0, contention_retry_after, reason="contention")
                probes += 1
                ref = coll.document(f"{cid}_{slot}")
                try:
                    ref.create(
                        {
                            "tenant_id": tenant_id,
                            "uid": uid,
                            "operation": policy.operation,
                            "window_start": start,
                            "window_key": cid,
                            "slot": slot,
                            "expires_at": expires_at,
                        },
                        retry=None,
                        timeout=budget(),
                    )
                    return RateDecision(True, policy.limit - slot - 1, retry_after)
                except gexc.AlreadyExists:
                    # Slot taken by a competing request: validate it, then try the next.
                    snap = ref.get(retry=None, timeout=budget())
                    if not snap.exists:
                        continue  # purged between create and get: retry the same slot
                    if not valid(snap.to_dict(), slot):
                        return RateDecision(False, 0, retry_after, reason="corrupt_state", corrupt_state=True)
                    slot += 1
                except gexc.Aborted:
                    # Retryable conflict: bounded retries with jitter, then fail closed.
                    conflicts += 1
                    if conflicts > MAX_CONTENTION_RETRIES:
                        return RateDecision(False, 0, contention_retry_after, reason="contention")
                    pause = random.uniform(0.01, 0.05) * conflicts
                    time.sleep(min(pause, max(0.0, deadline - time.monotonic())))
            return RateDecision(False, 0, retry_after, reason="limit_reached")

        try:
            decision = decide()
        except _BudgetExhausted:
            decision = RateDecision(False, 0, contention_retry_after, reason="contention")
        except Exception as exc:  # anything else is a genuine storage failure
            _emit("storage_unavailable", policy.operation, error_type=type(exc).__name__)
            raise RateLimitUnavailable("rate-limit state unavailable") from exc
        _emit(decision.reason, policy.operation)
        return decision

    def purge_expired(self, batch_size: int = 200, now: float | None = None) -> int:
        """Deletes counter documents whose `expires_at` has passed. The Firestore
        TTL policy does this automatically in production; this is the manual path."""
        from google.cloud.firestore_v1.base_query import FieldFilter

        cutoff = datetime.fromtimestamp(time.time() if now is None else now, tz=UTC)
        deleted = 0
        query = self._db.collection(self._collection).where(filter=FieldFilter("expires_at", "<", cutoff)).limit(batch_size)
        for doc in query.stream():
            doc.reference.delete()
            deleted += 1
        return deleted

    @staticmethod
    def ttl_cutoff(window_end: int) -> datetime:
        return datetime.fromtimestamp(window_end, tz=UTC) + timedelta(seconds=EXPIRY_GRACE_SECONDS)
