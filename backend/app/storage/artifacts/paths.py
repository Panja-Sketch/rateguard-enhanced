"""Tenant-prefixed artifact object keys (locked doc 14.2).

    tenants/{tenant_id}/sources/{source_id}/raw/{artifact_id}
    tenants/{tenant_id}/sources/{source_id}/compiled/{artifact_id}
    tenants/{tenant_id}/missions/{mission_id}/evidence/{artifact_id}

`ArtifactKey` is the *only* way to address an artifact. Every component is
validated against a strict allowlist at construction, so no key can contain a
slash, `.`/`..`, percent-encoding, whitespace, a NUL, or any other character
that could alter the object path. The tenant component must come from the
authenticated server context (`AuthenticatedUser.tenant_id`); nothing in this
module reads a request, body, or filename. Original filenames are stored only
as sanitized metadata and never influence a path.
"""

import re
from dataclasses import dataclass
from typing import Literal

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")

Scope = Literal["sources", "missions"]
Kind = Literal["raw", "compiled", "evidence"]

_ALLOWED_KINDS: dict[str, frozenset[str]] = {
    "sources": frozenset({"raw", "compiled"}),
    "missions": frozenset({"evidence"}),
}

_FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9._ -]")


class ArtifactPathError(ValueError):
    """An identifier or scope/kind combination is not allowed in an object path."""


def validate_identifier(value: str, what: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ArtifactPathError(f"Invalid {what} for an artifact path.")
    return value


def sanitize_filename(name: str) -> str:
    """Display-only metadata: never used to build a path."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _FILENAME_UNSAFE.sub("_", base).strip(" .")[:120]
    return cleaned or "artifact"


@dataclass(frozen=True)
class ArtifactKey:
    tenant_id: str
    scope: str
    scope_id: str
    kind: str
    artifact_id: str

    def __post_init__(self) -> None:
        validate_identifier(self.tenant_id, "tenant id")
        validate_identifier(self.scope_id, f"{self.scope} id")
        validate_identifier(self.artifact_id, "artifact id")
        allowed = _ALLOWED_KINDS.get(self.scope)
        if allowed is None or self.kind not in allowed:
            raise ArtifactPathError("Invalid scope/kind combination for an artifact path.")

    @property
    def object_path(self) -> str:
        return f"tenants/{self.tenant_id}/{self.scope}/{self.scope_id}/{self.kind}/{self.artifact_id}"

    @property
    def legacy_object_path(self) -> str:
        """Pre-tenant layout (`artifacts/{artifact_id}`), readable only through the
        explicit legacy-tenant assignment (see BaseArtifactStore)."""
        return f"artifacts/{self.artifact_id}"
