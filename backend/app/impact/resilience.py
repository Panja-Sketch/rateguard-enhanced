"""Retry classification, connector circuit breaker and QPS pacing for impact
batches. Pure, dependency-light and unit-testable with injected clocks."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from app.connectors.errors import ConnectorException, ConnectorFailureCategory

CIRCUIT_OPEN_CODE = "CONNECTOR_CIRCUIT_OPEN"

# Item-level codes a batch-capable connector may return for a transient problem.
TRANSIENT_ITEM_CODES = frozenset({"TEMPORARILY_UNAVAILABLE", "RATE_LIMITED"})
# Authentication/authorization problems: never retried without a configuration change.
AUTH_CODES = frozenset({"CONNECTOR_AUTH_DENIED", "CONNECTOR_AUTH_UNAVAILABLE"})
# Transient even though they are not RETRYABLE-category client errors.
_TRANSIENT_CODES = frozenset({"CONNECTOR_TARGET_BUDGET_EXCEEDED", CIRCUIT_OPEN_CODE}) | TRANSIENT_ITEM_CODES


def is_transient(exc: ConnectorException) -> bool:
    """Retry only 429 / 5xx / timeout / temporary network failures (already
    classified RETRYABLE by the client) plus the explicit transient codes.
    Schema, version, authentication and other 4xx failures are permanent."""
    return exc.category == ConnectorFailureCategory.RETRYABLE or exc.error.code in _TRANSIENT_CODES


def is_auth_failure(code: str) -> bool:
    return code in AUTH_CODES


# Failures that will affect every row until configuration changes: fail fast
# for the rest of the batch instead of hammering the connector.
SYSTEMIC_PERMANENT_CODES = AUTH_CODES | frozenset({
    "CONNECTOR_NOT_REGISTERED",
    "CONNECTOR_ENGINE_VERSION_NOT_ALLOWED",
    "CONNECTOR_ENGINE_VERSION_MISMATCH",
    "CONNECTOR_SCHEMA_VIOLATION",
    "CONNECTOR_UNEXPECTED_REDIRECT",
    "CONNECTOR_REQUEST_ID_MISMATCH",
})


def is_systemic_permanent(code: str) -> bool:
    return code in SYSTEMIC_PERMANENT_CODES


class CircuitBreaker:
    """Opens after `threshold` consecutive infrastructure failures; while open,
    calls fail fast until `cooldown` elapses, then a single trial call is let
    through. An authentication failure trips it permanently for its lifetime
    (a retry cannot help without a configuration change)."""

    def __init__(
        self, threshold: int, cooldown_seconds: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._threshold = threshold
        self._cooldown = cooldown_seconds
        self._clock = clock
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._permanent_code: str | None = None
        self.times_opened = 0

    @property
    def permanent_code(self) -> str | None:
        return self._permanent_code

    def allow(self) -> bool:
        if self._permanent_code is not None:
            return False
        if self._opened_at is None:
            return True
        if self._clock() - self._opened_at >= self._cooldown:
            # Half-open: allow a trial; a failure re-opens immediately.
            self._opened_at = None
            self._consecutive_failures = self._threshold - 1
            return True
        return False

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._opened_at = None

    def record_failure(self, code: str, *, permanent: bool = False) -> None:
        if permanent or is_systemic_permanent(code):
            if self._permanent_code is None:
                self.times_opened += 1
            self._permanent_code = code
            return
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._threshold and self._opened_at is None:
            self._opened_at = self._clock()
            self.times_opened += 1


class AsyncPacer:
    """Spaces request starts at most `qps` per second across concurrent tasks."""

    def __init__(self, qps: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._interval = 1.0 / qps if qps > 0 else 0.0
        self._clock = clock
        self._next_slot = 0.0
        self._lock: asyncio.Lock | None = None

    async def acquire(self) -> None:
        if self._interval <= 0:
            return
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            now = self._clock()
            wait = self._next_slot - now
            self._next_slot = max(now, self._next_slot) + self._interval
        if wait > 0:
            await asyncio.sleep(wait)
