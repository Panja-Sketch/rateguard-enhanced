"""Pure, unit-testable backoff/jitter and retry-classification helpers
(locked doc section 16.2: "Cap delivery attempts at five, use exponential
backoff with jitter.").

`compute_backoff_delay` is a pure function — no sleeping, no I/O — so a
test can assert delay bounds deterministically by injecting a fixed
`random_fn` instead of mocking `random.random` globally or actually
sleeping through real exponential delays.
"""

from __future__ import annotations

import random
from collections.abc import Callable

MAX_ATTEMPTS = 5
BASE_DELAY_SECONDS = 0.5
MAX_DELAY_SECONDS = 30.0

# Locked doc section 16.2: "transient Vertex 429/5xx, connector timeout,
# temporary GCP service error" are retryable. Applied here to HTTP status
# codes returned by the target rating engine.
RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


def compute_backoff_delay(
    attempt: int,
    *,
    base: float = BASE_DELAY_SECONDS,
    cap: float = MAX_DELAY_SECONDS,
    random_fn: Callable[[], float] = random.random,
) -> float:
    """"Full jitter" exponential backoff: ``delay = uniform(0, min(cap, base * 2**(attempt-1)))``.

    `attempt` is 1-indexed (the first retry attempt is `attempt=1`).
    `random_fn` must return a value in `[0, 1)`; injected in tests for
    deterministic bounds checking without real randomness or real sleeping.
    """
    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    ceiling = min(cap, base * (2 ** (attempt - 1)))
    return random_fn() * ceiling


def is_retryable_http_status(status_code: int) -> bool:
    return status_code in RETRYABLE_STATUS_CODES or 500 <= status_code < 600
