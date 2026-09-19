"""`GET /api/v1/me` -- who the *server* says the caller is.

The web app uses this only to label the session and tailor navigation; it is
never an authorization decision (the backend re-checks role and tenant on every
request). Values come from the verified token plus the server-controlled
`users/{uid}` record, never from anything the client sent."""

from typing import Any

from fastapi import APIRouter, Depends

from app.auth import AuthenticatedUser, require_read

router = APIRouter(prefix="/api/v1", tags=["session"])


@router.get("/me")
def get_current_session(user: AuthenticatedUser = Depends(require_read)) -> dict[str, Any]:
    return {
        "uid": user.uid,
        "email": user.email,
        "tenant_id": user.tenant_id,
        "role": user.role.value,
    }
