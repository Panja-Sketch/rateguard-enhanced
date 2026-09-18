"""Pure backoff/jitter unit tests (locked doc section 16.2). No real
sleeping anywhere in this file - `compute_backoff_delay` is a pure
function."""

import pytest

from app.connectors.retry import (
    BASE_DELAY_SECONDS,
    MAX_ATTEMPTS,
    MAX_DELAY_SECONDS,
    compute_backoff_delay,
    is_retryable_http_status,
)


def test_max_attempts_is_five_per_locked_doc():
    assert MAX_ATTEMPTS == 5


def test_delay_is_zero_when_random_fn_returns_zero():
    for attempt in range(1, 6):
        assert compute_backoff_delay(attempt, random_fn=lambda: 0.0) == 0.0


def test_delay_grows_exponentially_with_full_jitter_at_random_one():
    # random_fn() == 1.0 (edge case) selects the full ceiling exactly.
    delays = [compute_backoff_delay(a, random_fn=lambda: 1.0) for a in range(1, 6)]
    expected = [
        min(MAX_DELAY_SECONDS, BASE_DELAY_SECONDS * (2 ** (a - 1))) for a in range(1, 6)
    ]
    assert delays == expected
    # strictly increasing until the cap is reached
    assert delays == sorted(delays)


def test_delay_never_exceeds_cap():
    for attempt in range(1, 20):
        delay = compute_backoff_delay(attempt, random_fn=lambda: 1.0)
        assert delay <= MAX_DELAY_SECONDS


def test_delay_bounded_between_zero_and_ceiling_for_mid_random_value():
    delay = compute_backoff_delay(3, random_fn=lambda: 0.5)
    ceiling = min(MAX_DELAY_SECONDS, BASE_DELAY_SECONDS * (2 ** 2))
    assert delay == pytest.approx(0.5 * ceiling)


def test_attempt_below_one_rejected():
    with pytest.raises(ValueError):
        compute_backoff_delay(0)


def test_is_retryable_http_status():
    assert is_retryable_http_status(429) is True
    assert is_retryable_http_status(500) is True
    assert is_retryable_http_status(503) is True
    assert is_retryable_http_status(400) is False
    assert is_retryable_http_status(401) is False
    assert is_retryable_http_status(200) is False
