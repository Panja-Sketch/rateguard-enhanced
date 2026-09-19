"""Rate limiters.

`FirestoreRateLimiter` is the Cloud Run implementation: a transactional
fixed-window counter shared by every instance, so limits hold no matter how
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

Failure behaviour (fail closed): a counter document that is corrupt (wrong types,
negative count, mismatched window) denies the action for the rest of that window
(the next window uses a fresh document); a storage failure raises
`RateLimitUnavailable`, which the API turns into 503 for these expensive actions.
"""

import hashlib
import json
import logging
import math
import random
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.ratelimit.policy import RateLimitPolicy

logger = logging.getLogger(__name__)

COLLECTION = "rate_limits"
# Documents outlive their window by this long before TTL/purge removes them.
EXPIRY_GRACE_SECONDS = 3600
# Contention handling for one counter document (bounded; then fail closed).
TXN_TRIES = 4
TXN_MAX_ATTEMPTS = 8


class RateLimitUnavailable(RuntimeError):
    """The shared counter could not be read/updated; callers fail closed."""


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: int
    corrupt_state: bool = False


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
                return RateDecision(False, 0, _retry_after(now, end))
            self._counters[cid] = (start, count + 1)
            return RateDecision(True, policy.limit - count - 1, _retry_after(now, end))


class FirestoreRateLimiter(RateLimiter):
    def __init__(self, db: Any, collection: str = COLLECTION) -> None:
        self._db = db
        self._collection = collection

    def hit(self, tenant_id: str, uid: str, policy: RateLimitPolicy, now: float | None = None) -> RateDecision:
        from google.cloud import firestore

        now = time.time() if now is None else now
        start, end = window_bounds(now, policy)
        cid = counter_id(policy, tenant_id, uid, start)
        retry_after = _retry_after(now, end)
        expires_at = datetime.fromtimestamp(end + EXPIRY_GRACE_SECONDS, tz=UTC)

        try:
            doc_ref = self._db.collection(self._collection).document(cid)

            @firestore.transactional
            def _txn(transaction: Any) -> RateDecision:
                snap = doc_ref.get(transaction=transaction)
                if not snap.exists:
                    transaction.set(
                        doc_ref,
                        {
                            "tenant_id": tenant_id,
                            "uid": uid,
                            "operation": policy.operation,
                            "window_start": start,
                            "count": 1,
                            "expires_at": expires_at,
                        },
                    )
                    return RateDecision(True, policy.limit - 1, retry_after)
                data = snap.to_dict() or {}
                count = data.get("count")
                if (
                    not isinstance(count, int)
                    or isinstance(count, bool)
                    or count < 0
                    or data.get("window_start") != start
                    or data.get("operation") != policy.operation
                    or data.get("tenant_id") != tenant_id
                    or data.get("uid") != uid
                ):
                    return RateDecision(False, 0, retry_after, corrupt_state=True)
                if count >= policy.limit:
                    return RateDecision(False, 0, retry_after)
                transaction.update(doc_ref, {"count": count + 1})
                return RateDecision(True, policy.limit - count - 1, retry_after)

            decision = None
            for attempt in range(TXN_TRIES):
                try:
                    decision = _txn(self._db.transaction(max_attempts=TXN_MAX_ATTEMPTS))
                    break
                except Exception:
                    # Hot-counter contention aborts transactions; back off with jitter
                    # and retry a bounded number of times before failing closed.
                    if attempt == TXN_TRIES - 1:
                        raise
                    time.sleep(random.uniform(0.02, 0.1) * (attempt + 1))
        except Exception as exc:
            logger.error("RATE_LIMIT_STORE_ERROR error_type=%s", type(exc).__name__)
            raise RateLimitUnavailable("rate-limit state unavailable") from exc
        if decision.corrupt_state:
            logger.error("RATE_LIMIT_STATE_CORRUPT operation=%s", policy.operation)
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
