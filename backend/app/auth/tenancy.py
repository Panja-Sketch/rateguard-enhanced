"""Tenant scoping helpers (locked doc 4.1.A, 14.1, acceptance scenario A12).

Disclosure policy: a record in another tenant is indistinguishable from a
record that does not exist -- both yield the same 404 -- so ids cannot be used
to enumerate other tenants' data.

Legacy (pre-tenant) records have `tenant_id is None`. They are *hidden from
everyone* by default. The server may explicitly assign them to one tenant via
`RATEGUARD_LEGACY_RECORD_TENANT_ID` (a deliberate operator decision, e.g. the
single demo tenant for existing demo missions); a user in any other tenant
still cannot see them.
"""

from typing import Any

from fastapi import HTTPException, status

from app.auth.models import AuthenticatedUser
from app.core.config import get_settings


def effective_tenant(record_tenant_id: str | None) -> str | None:
    """The tenant a record belongs to, applying the explicit legacy rule."""
    if record_tenant_id:
        return record_tenant_id
    return get_settings().legacy_record_tenant_id or None


def is_visible_to(record_tenant_id: str | None, user: AuthenticatedUser) -> bool:
    owner = effective_tenant(record_tenant_id)
    return owner is not None and owner == user.tenant_id


def include_legacy_for(user: AuthenticatedUser) -> bool:
    """True only when the server explicitly assigned pre-tenant records to this
    caller's tenant (RATEGUARD_LEGACY_RECORD_TENANT_ID)."""
    legacy = get_settings().legacy_record_tenant_id
    return bool(legacy) and legacy == user.tenant_id


def not_found(kind: str, identifier: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"{kind} '{identifier}' not found.",
    )


def get_scoped_run(store: Any, run_id: str, user: AuthenticatedUser, kind: str = "Assurance mission") -> Any:
    """Fetches a run record only if it belongs to the user's tenant; otherwise
    raises the same 404 as a genuinely missing record."""
    record = store.get_run_for_tenant(run_id, user.tenant_id, include_legacy=include_legacy_for(user))
    if record is None:
        raise not_found(kind, run_id)
    return record
