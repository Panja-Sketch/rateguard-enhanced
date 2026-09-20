"""FastAPI integration: `rate_limited("mission_create")` as a route dependency.

Place it AFTER the role dependency in the route signature so requests that are
unauthenticated or forbidden are rejected first and never consume quota."""

import logging
import os
from collections.abc import Callable
from functools import lru_cache

from fastapi import Depends, HTTPException, status

from app.auth import AuthenticatedUser, get_current_user
from app.core.config import get_settings
from app.ratelimit.limiter import (
    FirestoreRateLimiter,
    InMemoryRateLimiter,
    RateLimiter,
    RateLimitUnavailable,
)
from app.ratelimit.policy import RateLimitPolicy, resolve_policies

logger = logging.getLogger(__name__)


@lru_cache
def get_policies() -> dict[str, RateLimitPolicy]:
    return resolve_policies(get_settings().rate_limits)


@lru_cache
def get_rate_limiter() -> RateLimiter:
    """Firestore (shared across Cloud Run instances) when the run store is
    Firestore; process-local otherwise (local development only)."""
    if os.getenv("RATEGUARD_RUN_STORE", "memory").lower() == "firestore":
        from google.cloud import firestore

        settings = get_settings()
        kwargs: dict = {"project": settings.google_cloud_project}
        db_id = os.getenv("RATEGUARD_FIRESTORE_DATABASE")
        if db_id and db_id != "(default)":
            kwargs["database"] = db_id
        return FirestoreRateLimiter(firestore.Client(**kwargs))
    return InMemoryRateLimiter()


def rate_limited(operation: str) -> Callable[..., None]:
    def _dependency(
        user: AuthenticatedUser = Depends(get_current_user),
        limiter: RateLimiter = Depends(get_rate_limiter),
    ) -> None:
        settings = get_settings()
        if not settings.rate_limit_enabled:
            return
        policy = get_policies()[operation]
        try:
            decision = limiter.hit(user.tenant_id, user.uid, policy)
        except RateLimitUnavailable:
            # Fail closed for expensive / security-sensitive actions.
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "RATE_LIMIT_UNAVAILABLE", "message": "Please try again shortly."},
                headers={"Retry-After": "5"},
            ) from None
        if not decision.allowed:
            logger.warning("RATE_LIMITED operation=%s reason=%s", operation, decision.reason or "limit_reached")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"code": "RATE_LIMITED", "message": "Too many requests. Please retry later."},
                headers={"Retry-After": str(decision.retry_after_seconds)},
            )

    _dependency.rate_limit_operation = operation  # type: ignore[attr-defined]
    return _dependency
