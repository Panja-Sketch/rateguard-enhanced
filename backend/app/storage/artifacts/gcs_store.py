import logging
from typing import Any

from app.storage.artifacts.interfaces import BaseArtifactStore
from app.storage.artifacts.local_store import LocalArtifactStore
from app.storage.artifacts.models import ArtifactDescriptor

logger = logging.getLogger(__name__)


class GCSArtifactStore(BaseArtifactStore):
    """Google Cloud Storage artifact store adapter for production/cloud deployments."""

    def __init__(
        self,
        bucket_name: str = "rateguard-ai-artifacts",
        project_id: str = "rateguard-ai",
        fallback_on_error: bool = True,
    ) -> None:
        self.bucket_name = bucket_name
        self.project_id = project_id
        self.fallback_on_error = fallback_on_error
        self._fallback_store = LocalArtifactStore()
        self._client: Any = None
        self._bucket: Any = None

        try:
            from google.cloud import storage

            self._client = storage.Client(project=project_id)
            self._bucket = self._client.bucket(bucket_name)
            logger.info("Successfully initialized GCS client for bucket '%s'", bucket_name)
        except Exception as e:
            if fallback_on_error:
                logger.warning(
                    "Failed to initialize GCS client (%s). Falling back to LocalArtifactStore.", e
                )
                self._client = None
                self._bucket = None
            else:
                raise

    def _gcs_path(self, artifact_id: str) -> str:
        """Derives the GCS object path from `artifact_id` alone, so any process
        can read back what another process saved. Never route this through the
        local descriptor cache (`self._fallback_store`) — that cache lives only
        in the memory of whichever process called `save_artifact`, and is empty
        in every other Cloud Run instance/service (e.g. the worker reading an
        artifact the API saved). Gating the GCS read behind that cache was the
        root cause of compiled sources being invisible to the worker."""
        return f"artifacts/{artifact_id}"

    def save_artifact(
        self,
        descriptor: ArtifactDescriptor,
        content: bytes,
    ) -> ArtifactDescriptor:
        self._fallback_store.save_artifact(descriptor, content)
        if self._bucket is not None:
            try:
                gcs_path = self._gcs_path(descriptor.artifact_id)
                blob = self._bucket.blob(gcs_path)
                blob.upload_from_string(content, content_type=descriptor.content_type)
                descriptor.storage_uri = f"gs://{self.bucket_name}/{gcs_path}"
            except Exception as e:
                logger.error("GCS error in save_artifact: %s", e)
                if not self.fallback_on_error:
                    raise
        return descriptor

    def get_artifact_content(self, artifact_id: str) -> bytes | None:
        if self._bucket is not None:
            try:
                blob = self._bucket.blob(self._gcs_path(artifact_id))
                if blob.exists():
                    return blob.download_as_bytes()
            except Exception as e:
                logger.error("GCS error in get_artifact_content: %s", e)
                if not self.fallback_on_error:
                    raise
        return self._fallback_store.get_artifact_content(artifact_id)

    def get_descriptor(self, artifact_id: str) -> ArtifactDescriptor | None:
        return self._fallback_store.get_descriptor(artifact_id)

    def exists(self, artifact_id: str) -> bool:
        return self._fallback_store.exists(artifact_id)
