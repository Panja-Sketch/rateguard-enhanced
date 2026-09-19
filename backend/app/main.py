from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.assurance import router as assurance_router
from app.api.connectors import router as connectors_router
from app.api.explanations import router as explanations_router
from app.api.health import router as health_router
from app.api.missions import router as missions_router
from app.api.session import router as session_router
from app.api.sources import router as sources_router
from app.api.system_status import router as system_status_router
from app.api.worker_endpoint import router as worker_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.request_limits import RequestSizeLimitMiddleware
from app.core.startup_checks import validate_startup_configuration

VALID_SERVICE_ROLES = ("all", "api", "worker")


def create_app(settings: Settings) -> FastAPI:
    if settings.service_role not in VALID_SERVICE_ROLES:
        raise ValueError(f"RATEGUARD_SERVICE_ROLE must be one of {VALID_SERVICE_ROLES}.")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        """Application lifecycle context manager."""
        configure_logging()
        # Fail startup (never serve) on a missing/unsupported/inconsistent model,
        # location, Firebase project, or CORS configuration.
        validate_startup_configuration(settings)
        yield

    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        lifespan=lifespan,
    )

    # CORS: explicit origin allowlist (validated at startup: no wildcard). Auth is
    # a bearer header, not a cookie, so credentialed CORS is unnecessary and off.
    # Methods/headers are limited to what the web app actually sends.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["Retry-After"],  # lets the web app show rate-limit waits
        max_age=600,
    )
    # Outermost: reject oversized bodies before they are buffered.
    application.add_middleware(RequestSizeLimitMiddleware, max_bytes=settings.max_request_bytes)

    application.include_router(health_router)
    if settings.service_role in ("all", "api"):
        application.include_router(session_router)
        application.include_router(assurance_router)
        application.include_router(missions_router)
        application.include_router(sources_router)
        application.include_router(connectors_router)
        application.include_router(explanations_router)
        application.include_router(system_status_router)
    if settings.service_role in ("all", "worker"):
        # Internal Pub/Sub push route: NOT Firebase-authenticated. Protected by
        # private Cloud Run IAM + the Pub/Sub OIDC service identity (see
        # docs/security/AUTHORIZATION_MATRIX.md). Never registered on `api`.
        application.include_router(worker_router)

    if settings.service_role in ("all", "api"):

        @application.get("/", summary="Root Status Endpoint")
        async def root() -> dict[str, str]:
            """Root endpoint returning service identification and status."""
            return {
                "service": settings.app_name,
                "version": settings.app_version,
                "status": "running",
            }

    return application


settings = get_settings()
app = create_app(settings)
