from abc import ABC, abstractmethod

from app.storage.artifacts.models import ArtifactDescriptor


class BaseArtifactStore(ABC):
    """Abstract interface for artifact blob storage adapters."""

    @abstractmethod
    def save_artifact(
        self,
        descriptor: ArtifactDescriptor,
        content: bytes,
    ) -> ArtifactDescriptor:
        """Saves a binary or text artifact blob."""
        pass

    @abstractmethod
    def get_artifact_content(self, artifact_id: str) -> bytes | None:
        """Retrieves raw content bytes for an artifact."""
        pass

    @abstractmethod
    def get_descriptor(self, artifact_id: str) -> ArtifactDescriptor | None:
        """Retrieves descriptor metadata for an artifact."""
        pass

    def get_artifact(self, artifact_id: str) -> ArtifactDescriptor | None:
        """Alias for get_descriptor."""
        return self.get_descriptor(artifact_id)

    @abstractmethod
    def exists(self, artifact_id: str) -> bool:
        """Checks if an artifact exists."""
        pass
