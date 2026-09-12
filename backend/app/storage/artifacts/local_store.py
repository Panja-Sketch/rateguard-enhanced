from pathlib import Path

from app.core.config import get_data_dir
from app.storage.artifacts.interfaces import BaseArtifactStore
from app.storage.artifacts.models import ArtifactCategory, ArtifactDescriptor


class LocalArtifactStore(BaseArtifactStore):
    """Local filesystem artifact store adapter for development and unit testing."""

    def __init__(self, base_dir: Path | None = None) -> None:
        if base_dir is None:
            data_dir = get_data_dir()
            base_dir = data_dir / "artifacts"
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._descriptors: dict[str, ArtifactDescriptor] = {}

    def save_artifact(
        self,
        descriptor: ArtifactDescriptor,
        content: bytes,
    ) -> ArtifactDescriptor:
        target_path = self.base_dir / descriptor.filename
        with open(target_path, "wb") as f:
            f.write(content)

        descriptor.storage_uri = str(target_path.resolve())
        descriptor.size_bytes = len(content)
        self._descriptors[descriptor.artifact_id] = descriptor
        return descriptor

    def get_artifact_content(self, artifact_id: str) -> bytes | None:
        desc = self._descriptors.get(artifact_id)
        if not desc:
            return None
        target_path = Path(desc.storage_uri)
        if target_path.exists():
            with open(target_path, "rb") as f:
                return f.read()
        return None

    def get_descriptor(self, artifact_id: str) -> ArtifactDescriptor | None:
        return self._descriptors.get(artifact_id)

    def get_artifact(self, artifact_id: str) -> ArtifactDescriptor | None:
        return self.get_descriptor(artifact_id)

    def exists(self, artifact_id: str) -> bool:
        return artifact_id in self._descriptors

    def list_artifacts(self, category: ArtifactCategory | None = None) -> list[ArtifactDescriptor]:
        if category is None:
            return list(self._descriptors.values())
        return [d for d in self._descriptors.values() if d.category == category]

    def delete_artifact(self, artifact_id: str) -> bool:
        desc = self._descriptors.pop(artifact_id, None)
        if desc:
            p = Path(desc.storage_uri)
            if p.exists():
                p.unlink(missing_ok=True)
            return True
        return False
