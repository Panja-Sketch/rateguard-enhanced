"""Centralized FastAPI authentication and authorization dependencies.

Every business route depends on `require_roles(...)` (or `get_current_user`),
which:

1. reads only `Authorization: Bearer <Firebase ID token>`;
2. verifies it through an injectable `TokenVerifier` (Firebase Admin + ADC in
   production; a fake in tests via `app.dependency_overrides`);
3. looks the verified uid up in the server-controlled user directory and takes
   role and tenant *only* from there -- never from the token's custom claims,
   the request body, query string, or any header other than Authorization;
4. fails closed with safe 401/403/503 responses.

The Pub/Sub worker endpoint deliberately does not use these dependencies; it
is protected by private Cloud Run IAM + Pub/Sub OIDC (see
docs/security/AUTHORIZATION_MATRIX.md).
"""

import logging
import re
import secrets
from collections.abc import Callable
from functools import lru_cache

from fastapi import Depends, Request

from app.auth.directory import UserDirectory, build_user_directory
from app.auth.errors import auth_unavailable, forbidden, unauthorized
from app.auth.models import AuthenticatedUser, Role
from app.auth.verifier import (
    FirebaseTokenVerifier,
    TokenServiceUnavailableError,
    TokenVerificationError,
    TokenVerifier,
)
from app.core.config import get_settings

logger = logging.getLogger(__name__)

MAX_TOKEN_LENGTH = 8192
_BEARER_RE = re.compile(r"^Bearer ([A-Za-z0-9._~+/=-]+)$")


@lru_cache
def get_token_verifier() -> TokenVerifier:
    settings = get_settings()
    return FirebaseTokenVerifier(settings.firebase_project_id, check_revoked=settings.firebase_check_revoked)


@lru_cache
def get_user_directory() -> UserDirectory:
    return build_user_directory(get_settings().google_cloud_project)


def _extract_bearer_token(request: Request) -> str:
    header = request.headers.get("authorization")
    if not header:
        raise unauthorized("AUTHENTICATION_REQUIRED")
    match = _BEARER_RE.match(header.strip())
    if not match or len(match.group(1)) > MAX_TOKEN_LENGTH:
        raise unauthorized("MALFORMED_AUTHORIZATION")
    return match.group(1)


def _demo_api_key_user(request: Request) -> AuthenticatedUser | None:
    """Optional scoped, read-only credential for external scripts (README
    "External API Access") -- an `X-RateGuard-Api-Key` header checked
    against `Settings.demo_api_key`. Returns None (falls through to the
    normal Firebase bearer-token path) whenever the feature is unconfigured
    or the header is absent, so it can never weaken the default auth
    posture. Always resolves to VIEWER (read-only) -- there is no way for a
    caller to obtain a higher role through this path."""
    settings = get_settings()
    if not settings.demo_api_key:
        return None
    supplied = request.headers.get("x-rateguard-api-key")
    if not supplied:
        return None
    if not secrets.compare_digest(supplied, settings.demo_api_key):
        logger.warning("AUTH_DENIED reason=invalid_demo_api_key path=%s", request.url.path)
        raise unauthorized("INVALID_API_KEY")
    return AuthenticatedUser(
        uid="demo-api-key", tenant_id=settings.demo_api_key_tenant_id, role=Role.VIEWER, email=None
    )


def get_current_user(
    request: Request,
    verifier: TokenVerifier = Depends(get_token_verifier),
    directory: UserDirectory = Depends(get_user_directory),
) -> AuthenticatedUser:
    demo_user = _demo_api_key_user(request)
    if demo_user is not None:
        return demo_user
    token = _extract_bearer_token(request)
    try:
        verified = verifier.verify(token)
    except TokenVerificationError as exc:
        logger.warning("AUTH_DENIED reason=%s path=%s", exc.reason, request.url.path)
        raise unauthorized("INVALID_TOKEN") from None
    except TokenServiceUnavailableError:
        logger.error("AUTH_VERIFIER_UNAVAILABLE path=%s", request.url.path)
        raise auth_unavailable() from None

    try:
        record = directory.get_user(verified.uid)
    except Exception:  # noqa: BLE001 - directory outage must fail closed, never leak
        logger.exception("AUTH_DIRECTORY_ERROR path=%s", request.url.path)
        raise auth_unavailable() from None

    if record is None:
        logger.warning("AUTH_DENIED reason=unknown_user path=%s", request.url.path)
        raise forbidden("ACCOUNT_NOT_PROVISIONED")
    if record.disabled:
        logger.warning("AUTH_DENIED reason=disabled_user path=%s", request.url.path)
        raise forbidden("ACCOUNT_DISABLED")

    return AuthenticatedUser(uid=record.uid, tenant_id=record.tenant_id, role=record.role, email=verified.email)


def require_roles(*allowed: Role) -> Callable[..., AuthenticatedUser]:
    """Dependency factory: the authenticated user must hold one of `allowed`.
    ADMIN is *not* implicitly added -- every route lists its roles explicitly
    so the access matrix is readable (and testable) in one place."""
    allowed_set = frozenset(allowed)

    def _dependency(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
        if user.role not in allowed_set:
            logger.warning("AUTH_DENIED reason=insufficient_role role=%s", user.role.value)
            raise forbidden("INSUFFICIENT_ROLE")
        return user

    _dependency.allowed_roles = allowed_set  # type: ignore[attr-defined]
    return _dependency


# --- The route-access matrix building blocks (documented in
# docs/security/AUTHORIZATION_MATRIX.md; asserted route-by-route in
# tests/auth/test_route_access_matrix.py). ---
ALL_ROLES = (Role.ADMIN, Role.RELEASE_OWNER, Role.CONSUMER_REVIEWER, Role.VIEWER)
READ_ROLES = ALL_ROLES
# Evidence *download* (an exportable bundle) excludes the read-only VIEWER.
EVIDENCE_DOWNLOAD_ROLES = (Role.ADMIN, Role.RELEASE_OWNER, Role.CONSUMER_REVIEWER)
RELEASE_WRITE_ROLES = (Role.ADMIN, Role.RELEASE_OWNER)
EXPLANATION_CREATE_ROLES = (Role.ADMIN, Role.RELEASE_OWNER, Role.CONSUMER_REVIEWER)
EXPLANATION_REVIEW_ROLES = (Role.ADMIN, Role.CONSUMER_REVIEWER)
ADMIN_ONLY = (Role.ADMIN,)

require_read = require_roles(*READ_ROLES)
require_release_write = require_roles(*RELEASE_WRITE_ROLES)
require_evidence_download = require_roles(*EVIDENCE_DOWNLOAD_ROLES)
require_explanation_create = require_roles(*EXPLANATION_CREATE_ROLES)
require_explanation_review = require_roles(*EXPLANATION_REVIEW_ROLES)
require_admin = require_roles(*ADMIN_ONLY)
