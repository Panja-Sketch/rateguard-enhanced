"""Typed authenticated-user context and the locked role set (locked doc 4.1.A)."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Role(StrEnum):
    ADMIN = "ADMIN"
    RELEASE_OWNER = "RELEASE_OWNER"
    CONSUMER_REVIEWER = "CONSUMER_REVIEWER"
    VIEWER = "VIEWER"


class UserRecord(BaseModel):
    """Server-controlled `users/{firebase_uid}` document. The only authority
    for role and tenant; nothing a client sends can create or alter it."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    uid: str
    tenant_id: str
    role: Role
    disabled: bool = False
    email: str | None = None


class AuthenticatedUser(BaseModel):
    """What every protected route receives. Built only from a verified
    Firebase ID token plus the server-side `UserRecord`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    uid: str
    tenant_id: str
    role: Role
    email: str | None = Field(default=None, description="From the verified token; informational only.")

    def has_role(self, *roles: Role) -> bool:
        return self.role in roles
