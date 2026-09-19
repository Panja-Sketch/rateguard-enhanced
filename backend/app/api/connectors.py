"""Locked doc section 13.2: `GET /connectors` — safe, credential-free
connector metadata only. No endpoint here accepts or returns a base URL,
auth header name, or token env-var name — those live only in the
server-side registry (`app.connectors.registry`). No endpoint accepts an
arbitrary remote URL from a caller (locked doc section 13.2 / section 11).
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import AuthenticatedUser, require_admin, require_read
from app.connectors.registry import ConnectorMetadata, list_connectors_metadata
from app.ratelimit import rate_limited

router = APIRouter(prefix="/api/v1/connectors", tags=["connectors"])


@router.get("", summary="List registered rating-engine connectors")
def list_connectors(user: AuthenticatedUser = Depends(require_read)) -> list[ConnectorMetadata]:
    """Safe metadata for every administrator-registered connector: id,
    display name, and allowed engine versions. Never a base URL or
    credential material."""
    return list_connectors_metadata()


@router.post("/{connector_id}/test", summary="Golden-case health test (admin only)")
async def test_connector(
    connector_id: str,
    user: AuthenticatedUser = Depends(require_admin),
    _quota: None = Depends(rate_limited("connector_test")),
) -> dict[str, Any]:
    """Runs the locked golden-case health check against a registered connector
    (locked doc 13.2). Admin only. The response carries pass/fail and the
    golden values only -- never a base URL, header name, or credential -- and
    the connector id must already be in the administrator's registry (no
    caller-supplied URL exists anywhere in this API)."""
    from app.connectors.health import test_connector_health

    if connector_id not in {c.connector_id for c in list_connectors_metadata()}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Connector '{connector_id}' not found.")
    return await test_connector_health(connector_id)
