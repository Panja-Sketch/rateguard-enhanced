"""Rate-limit policies: fixed window per (tenant, authenticated uid, operation).

Challenge defaults are deliberately low for the expensive or security-sensitive
actions (a demo needs a handful of missions, not hundreds). Override with
`RATEGUARD_RATE_LIMITS='{"mission_create":"10/3600"}'` (`limit/window_seconds`);
values are range-checked at startup and unknown operations are rejected.
"""

from dataclasses import dataclass

# operation -> (limit, window_seconds)
DEFAULT_POLICIES: dict[str, tuple[int, int]] = {
    "connector_test": (5, 3600),  # strictest: calls an external engine
    "mission_create": (10, 3600),  # strict: queues billable Gemini/BigQuery work (also retry)
    "source_upload": (30, 3600),
    "source_compile": (30, 3600),
    "explanation_create": (20, 3600),
    "evidence_download": (30, 3600),
    "source_download": (60, 3600),
}

MAX_LIMIT = 10_000
MAX_WINDOW_SECONDS = 86_400


@dataclass(frozen=True)
class RateLimitPolicy:
    operation: str
    limit: int
    window_seconds: int


class RateLimitConfigError(ValueError):
    """Raised at startup for an unknown operation or an out-of-range/unparseable policy."""


def parse_policy(operation: str, raw: str) -> RateLimitPolicy:
    try:
        limit_s, window_s = raw.split("/", 1)
        limit, window = int(limit_s), int(window_s)
    except ValueError as exc:
        raise RateLimitConfigError(f"Rate limit for '{operation}' must look like '<limit>/<window_seconds>'.") from exc
    if not (1 <= limit <= MAX_LIMIT):
        raise RateLimitConfigError(f"Rate limit for '{operation}' must be between 1 and {MAX_LIMIT} requests.")
    if not (1 <= window <= MAX_WINDOW_SECONDS):
        raise RateLimitConfigError(f"Rate-limit window for '{operation}' must be between 1 and {MAX_WINDOW_SECONDS} seconds.")
    return RateLimitPolicy(operation, limit, window)


def resolve_policies(overrides: dict[str, str] | None = None) -> dict[str, RateLimitPolicy]:
    policies = {op: RateLimitPolicy(op, lim, win) for op, (lim, win) in DEFAULT_POLICIES.items()}
    for operation, raw in (overrides or {}).items():
        if operation not in DEFAULT_POLICIES:
            raise RateLimitConfigError(f"Unknown rate-limited operation '{operation}'.")
        policies[operation] = parse_policy(operation, str(raw))
    return policies
