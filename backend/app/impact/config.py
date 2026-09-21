"""Validated runtime budgets for connector-backed portfolio impact.

Every value is a candidate-safe environment variable (`RATEGUARD_IMPACT_*`).
Unset means the documented conservative default; a set value must parse and
lie inside its range, and the combined concurrency ceiling must respect the
connector's global limit, otherwise startup fails (`RuntimeConfigError`).
Nothing here reads or logs a credential.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from decimal import Decimal, InvalidOperation

from app.core.runtime_config import RuntimeConfigError, _merged_environment

# name: (cast, minimum, maximum, default)
IMPACT_SPECS: dict[str, tuple[type, float, float, float]] = {
    "RATEGUARD_IMPACT_MAX_POLICIES": (int, 1, 100_000, 50_000),
    "RATEGUARD_IMPACT_BATCH_SIZE": (int, 10, 250, 200),
    "RATEGUARD_IMPACT_MAX_INFLIGHT_BATCHES": (int, 1, 10, 3),
    "RATEGUARD_IMPACT_REQUEST_CONCURRENCY_PER_BATCH": (int, 1, 20, 5),
    "RATEGUARD_IMPACT_MAX_QPS": (float, 1.0, 500.0, 120.0),
    "RATEGUARD_IMPACT_REQUEST_TIMEOUT_SECONDS": (float, 1.0, 30.0, 10.0),
    "RATEGUARD_IMPACT_BATCH_TIMEOUT_SECONDS": (int, 30, 540, 240),
    "RATEGUARD_IMPACT_MAX_RETRY_ATTEMPTS": (int, 1, 5, 3),
    "RATEGUARD_IMPACT_MAX_BATCH_ATTEMPTS": (int, 1, 6, 3),
    "RATEGUARD_IMPACT_MAX_MISSION_SECONDS": (int, 30, 570, 540),
    "RATEGUARD_IMPACT_MAX_MISMATCH_EXAMPLES": (int, 0, 100, 20),
    "RATEGUARD_IMPACT_MAX_EVIDENCE_BYTES": (int, 10_000, 5_000_000, 512_000),
    "RATEGUARD_IMPACT_BREAKER_THRESHOLD": (int, 2, 100, 8),
    "RATEGUARD_IMPACT_BREAKER_COOLDOWN_SECONDS": (float, 1.0, 300.0, 20.0),
    "RATEGUARD_IMPACT_POLL_INTERVAL_SECONDS": (float, 0.05, 30.0, 2.0),
}

# Combined ceiling on simultaneously outstanding connector requests
# (in-flight batches x per-batch concurrency).
MAX_GLOBAL_CONCURRENT_REQUESTS = 20
PREMIUM_TOLERANCE_VAR = "RATEGUARD_IMPACT_PREMIUM_TOLERANCE"


@dataclass(frozen=True)
class ImpactConfig:
    max_policies: int = 50_000
    batch_size: int = 200
    max_inflight_batches: int = 3
    request_concurrency_per_batch: int = 5
    max_qps: float = 120.0
    request_timeout_seconds: float = 10.0
    batch_timeout_seconds: int = 240
    max_retry_attempts: int = 3
    max_batch_attempts: int = 3
    max_mission_seconds: int = 540
    max_mismatch_examples: int = 20
    max_evidence_bytes: int = 512_000
    breaker_threshold: int = 8
    breaker_cooldown_seconds: float = 20.0
    poll_interval_seconds: float = 2.0
    premium_tolerance: Decimal = Decimal("0.00")

    @property
    def global_request_concurrency(self) -> int:
        return self.max_inflight_batches * self.request_concurrency_per_batch

    @property
    def per_batch_qps(self) -> float:
        return self.max_qps / self.max_inflight_batches

    def validate(self) -> None:
        problems: list[str] = []
        if self.global_request_concurrency > MAX_GLOBAL_CONCURRENT_REQUESTS:
            problems.append(
                "RATEGUARD_IMPACT_MAX_INFLIGHT_BATCHES x RATEGUARD_IMPACT_REQUEST_CONCURRENCY_PER_BATCH "
                f"must not exceed {MAX_GLOBAL_CONCURRENT_REQUESTS}."
            )
        if self.batch_timeout_seconds >= self.max_mission_seconds:
            problems.append(
                "RATEGUARD_IMPACT_BATCH_TIMEOUT_SECONDS must be smaller than RATEGUARD_IMPACT_MAX_MISSION_SECONDS."
            )
        if self.premium_tolerance < 0 or self.premium_tolerance > Decimal("1.00"):
            problems.append(f"{PREMIUM_TOLERANCE_VAR} must be between 0 and 1.00.")
        if problems:
            raise RuntimeConfigError("Invalid impact configuration: " + " ".join(problems))


_FIELD_BY_ENV = {
    "RATEGUARD_IMPACT_MAX_POLICIES": "max_policies",
    "RATEGUARD_IMPACT_BATCH_SIZE": "batch_size",
    "RATEGUARD_IMPACT_MAX_INFLIGHT_BATCHES": "max_inflight_batches",
    "RATEGUARD_IMPACT_REQUEST_CONCURRENCY_PER_BATCH": "request_concurrency_per_batch",
    "RATEGUARD_IMPACT_MAX_QPS": "max_qps",
    "RATEGUARD_IMPACT_REQUEST_TIMEOUT_SECONDS": "request_timeout_seconds",
    "RATEGUARD_IMPACT_BATCH_TIMEOUT_SECONDS": "batch_timeout_seconds",
    "RATEGUARD_IMPACT_MAX_RETRY_ATTEMPTS": "max_retry_attempts",
    "RATEGUARD_IMPACT_MAX_BATCH_ATTEMPTS": "max_batch_attempts",
    "RATEGUARD_IMPACT_MAX_MISSION_SECONDS": "max_mission_seconds",
    "RATEGUARD_IMPACT_MAX_MISMATCH_EXAMPLES": "max_mismatch_examples",
    "RATEGUARD_IMPACT_MAX_EVIDENCE_BYTES": "max_evidence_bytes",
    "RATEGUARD_IMPACT_BREAKER_THRESHOLD": "breaker_threshold",
    "RATEGUARD_IMPACT_BREAKER_COOLDOWN_SECONDS": "breaker_cooldown_seconds",
    "RATEGUARD_IMPACT_POLL_INTERVAL_SECONDS": "poll_interval_seconds",
}


def resolve_impact_config(environ: Mapping[str, str] | None = None) -> ImpactConfig:
    """Parses and validates the impact budgets; raises `RuntimeConfigError`
    naming every offending variable (never a value that could be a secret)."""
    env = _merged_environment(environ, None)
    values: dict[str, object] = {}
    problems: list[str] = []
    for name, (cast, low, high, _default) in IMPACT_SPECS.items():
        raw = env.get(name, "").strip()
        if not raw:
            continue
        try:
            value = cast(raw)
        except ValueError:
            problems.append(f"{name} must be a valid {cast.__name__}.")
            continue
        if not (low <= value <= high):
            problems.append(f"{name} must be between {low} and {high}.")
            continue
        values[_FIELD_BY_ENV[name]] = value

    raw_tol = env.get(PREMIUM_TOLERANCE_VAR, "").strip()
    if raw_tol:
        try:
            values["premium_tolerance"] = Decimal(raw_tol)
        except InvalidOperation:
            problems.append(f"{PREMIUM_TOLERANCE_VAR} must be a valid decimal.")
    if problems:
        raise RuntimeConfigError("Invalid impact configuration: " + " ".join(problems))

    config = ImpactConfig(**values)  # type: ignore[arg-type]
    config.validate()
    return config


def budget_snapshot(config: ImpactConfig) -> dict[str, object]:
    """Non-sensitive budget values recorded in mission evidence."""
    return {f.name: str(getattr(config, f.name)) for f in fields(config)}
