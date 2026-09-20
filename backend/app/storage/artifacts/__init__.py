import os

from app.storage.artifacts.gcs_store import GCSArtifactStore
from app.storage.artifacts.interfaces import BaseArtifactStore
from app.storage.artifacts.local_store import LocalArtifactStore
from app.storage.artifacts.models import ArtifactCategory, ArtifactDescriptor
from app.storage.artifacts.paths import ArtifactKey, ArtifactPathError

_global_artifact_store: BaseArtifactStore | None = None


def get_artifact_store() -> BaseArtifactStore:
    """Factory function returning the configured ArtifactStore adapter instance."""
    global _global_artifact_store
    store_type = os.getenv("RATEGUARD_ARTIFACT_STORE", "local").lower()
    if store_type == "gcs":
        bucket = os.getenv("RATEGUARD_GCS_BUCKET", "rateguard-enhanced-artifacts")
        project = os.getenv("RATEGUARD_GOOGLE_CLOUD_PROJECT", "rateguard-enhanced")
        return GCSArtifactStore(bucket_name=bucket, project_id=project)

    if _global_artifact_store is None:
        _global_artifact_store = LocalArtifactStore()
    return _global_artifact_store


__all__ = [
    "ArtifactCategory",
    "ArtifactKey",
    "ArtifactPathError",
    "ArtifactDescriptor",
    "BaseArtifactStore",
    "GCSArtifactStore",
    "LocalArtifactStore",
    "get_artifact_store",
]
