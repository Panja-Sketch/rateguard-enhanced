from abc import ABC, abstractmethod

from app.core.config import get_settings
from app.storage.artifacts.models import ArtifactDescriptor
from app.storage.artifacts.paths import ArtifactKey, ArtifactPathError


class BaseArtifactStore(ABC):
    """Tenant-aware artifact blob storage.

    Every operation is addressed by an `ArtifactKey` whose first component is
    the caller's tenant, so a tenant can only ever read objects under its own
    `tenants/{tenant_id}/…` prefix: authorization is structural, not a filter
    applied afterwards. A missing object and another tenant's object are
    indistinguishable (`None` / `False`).

    Pre-tenant ("legacy") objects live at `artifacts/{artifact_id}`. They are
    readable only when the server explicitly assigns legacy records to the
    caller's tenant (`RATEGUARD_LEGACY_RECORD_TENANT_ID`), never to every tenant.
    """

    @staticmethod
    def legacy_readable_by(key: ArtifactKey) -> bool:
        legacy = get_settings().legacy_record_tenant_id
        return bool(legacy) and legacy == key.tenant_id and key.kind in ("raw", "compiled")

    @staticmethod
    def descriptor_key(descriptor: ArtifactDescriptor) -> ArtifactKey:
        """The key a descriptor is stored under. New artifacts must be tenant-scoped;
        a descriptor without tenant/scope/kind is rejected rather than defaulted."""
        if not (descriptor.tenant_id and descriptor.scope and descriptor.scope_id and descriptor.kind):
            raise ArtifactPathError("Artifacts must be saved with tenant, scope, scope id and kind.")
        return ArtifactKey(
            tenant_id=descriptor.tenant_id,
            scope=descriptor.scope,
            scope_id=descriptor.scope_id,
            kind=descriptor.kind,
            artifact_id=descriptor.artifact_id,
        )

    @abstractmethod
    def save_artifact(self, descriptor: ArtifactDescriptor, content: bytes) -> ArtifactDescriptor:
        """Saves a blob at the descriptor's tenant-prefixed key."""

    @abstractmethod
    def get_artifact_content(self, key: ArtifactKey) -> bytes | None:
        """Raw bytes for `key`, or None if absent (or owned by another tenant)."""

    @abstractmethod
    def get_descriptor(self, key: ArtifactKey) -> ArtifactDescriptor | None:
        """Metadata for `key`, or None if absent (or owned by another tenant)."""

    def get_artifact(self, key: ArtifactKey) -> ArtifactDescriptor | None:
        return self.get_descriptor(key)

    @abstractmethod
    def exists(self, key: ArtifactKey) -> bool:
        """True only when `key` exists under its own tenant prefix."""
