"""Locked doc section 13.2: `GET /connectors` — safe, credential-free
connector metadata only. No endpoint here accepts or returns a base URL,
auth header name, or token env-var name — those live only in the
server-side registry (`app.connectors.registry`). No endpoint accepts an
arbitrary remote URL from a caller (locked doc section 13.2 / section 11).
"""

from fastapi import APIRouter

from app.connectors.registry import ConnectorMetadata, list_connectors_metadata

router = APIRouter(prefix="/api/v1/connectors", tags=["connectors"])


@router.get("", summary="List registered rating-engine connectors")
def list_connectors() -> list[ConnectorMetadata]:
    """Safe metadata for every administrator-registered connector: id,
    display name, and allowed engine versions. Never a base URL or
    credential material."""
    return list_connectors_metadata()
