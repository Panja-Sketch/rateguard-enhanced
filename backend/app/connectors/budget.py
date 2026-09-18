"""Mission-level target budget (locked doc section 8.2: "mission-level
target budget 60 seconds"), enforceable across a *batch* of connector calls
belonging to one mission's target-execution stage — not just a single
request's own connect/read timeout.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from app.connectors.errors import ConnectorException, ConnectorFailureCategory

MISSION_TARGET_BUDGET_SECONDS = 60.0


class TargetBudget:
    """Pass one instance across every connector call in a mission's
    target-execution stage. `check()` raises a typed, `REVIEW_REQUIRED`
    `ConnectorException` once the cumulative wall-clock budget is
    exhausted — a target that would otherwise keep being retried past the
    mission-level ceiling instead fails closed, per locked doc section
    16.3 ("If an external dependency exceeds its budget, the mission
    returns REVIEW_REQUIRED or FAILED; it never guesses.")."""

    def __init__(
        self,
        budget_seconds: float = MISSION_TARGET_BUDGET_SECONDS,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._budget_seconds = budget_seconds
        self._clock = clock
        self._started_at = clock()

    def elapsed(self) -> float:
        return self._clock() - self._started_at

    def remaining(self) -> float:
        return self._budget_seconds - self.elapsed()

    def check(self) -> None:
        remaining = self.remaining()
        if remaining <= 0:
            raise ConnectorException(
                code="CONNECTOR_TARGET_BUDGET_EXCEEDED",
                message=(
                    f"Mission-level target budget of {self._budget_seconds}s "
                    "was exhausted before this connector call could complete."
                ),
                category=ConnectorFailureCategory.REVIEW_REQUIRED,
            )
