from pathlib import Path

from app.core.config import get_data_dir
from app.storage.artifacts.interfaces import BaseArtifactStore
from app.storage.artifacts.models import ArtifactCategory, ArtifactDescriptor
from app.storage.artifacts.paths import ArtifactKey, ArtifactPathError, sanitize_filename


class LocalArtifactStore(BaseArtifactStore):
    """Local filesystem adapter (development/tests) with the same tenant-prefixed
    layout and the same authorization semantics as the GCS adapter: objects are
    stored and read under `tenants/{tenant_id}/…` only, keyed by `ArtifactKey`.
    Descriptors are process-local; the caller's tenant is part of every lookup,
    so another tenant's object is simply absent. There is no legacy layout here
    (legacy local artifacts were process-local and never survived a restart)."""

    def __init__(self, base_dir: Path | None = None) -> None:
        if base_dir is None:
            data_dir = get_data_dir()
            base_dir = data_dir / "artifacts"
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._root = self.base_dir.resolve()
        self._descriptors: dict[str, ArtifactDescriptor] = {}

    def _path_for(self, key: ArtifactKey) -> Path:
        target = (self._root / key.object_path).resolve()
        # Defense in depth: ArtifactKey already forbids traversal characters.
        if self._root not in target.parents:
            raise ArtifactPathError("Resolved artifact path escapes the storage root.")
        return target

    def save_artifact(self, descriptor: ArtifactDescriptor, content: bytes) -> ArtifactDescriptor:
        key = self.descriptor_key(descriptor)
        target = self._path_for(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        descriptor.filename = sanitize_filename(descriptor.filename)
        descriptor.storage_uri = str(target)
        descriptor.size_bytes = len(content)
        self._descriptors[key.object_path] = descriptor
        return descriptor

    def get_artifact_content(self, key: ArtifactKey) -> bytes | None:
        if key.object_path not in self._descriptors:
            return None
        target = self._path_for(key)
        return target.read_bytes() if target.is_file() else None

    def get_descriptor(self, key: ArtifactKey) -> ArtifactDescriptor | None:
        return self._descriptors.get(key.object_path)

    def exists(self, key: ArtifactKey) -> bool:
        return key.object_path in self._descriptors

    def list_artifacts(self, tenant_id: str, category: ArtifactCategory | None = None) -> list[ArtifactDescriptor]:
        """Only the given tenant's artifacts."""
        return [
            d
            for d in self._descriptors.values()
            if d.tenant_id == tenant_id and (category is None or d.category == category)
        ]

    def delete_artifact(self, key: ArtifactKey) -> bool:
        desc = self._descriptors.pop(key.object_path, None)
        if desc is None:
            return False
        self._path_for(key).unlink(missing_ok=True)
        return True
