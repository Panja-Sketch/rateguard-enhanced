import logging
from typing import Any

from app.storage.artifacts.interfaces import BaseArtifactStore
from app.storage.artifacts.local_store import LocalArtifactStore
from app.storage.artifacts.models import ArtifactCategory, ArtifactDescriptor
from app.storage.artifacts.paths import ArtifactKey, sanitize_filename

logger = logging.getLogger(__name__)


class GCSArtifactStore(BaseArtifactStore):
    """Google Cloud Storage adapter. Objects live at
    `tenants/{tenant_id}/{sources|missions}/{id}/{raw|compiled|evidence}/{artifact_id}`
    (locked doc 14.2). Because the tenant is part of the object name and every
    read is addressed by the *caller's* tenant, cross-tenant access is impossible
    by construction — no ownership check is needed after the fetch, and another
    tenant's object is indistinguishable from a missing one.

    Descriptor metadata is stored as GCS object metadata so any Cloud Run
    process (API or worker) can read it back; nothing depends on a per-process
    cache (the historical worker-can't-see-API-artifacts failure)."""

    def __init__(
        self,
        bucket_name: str = "rateguard-enhanced-artifacts",
        project_id: str = "rateguard-enhanced",
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
                logger.warning("Failed to initialize GCS client (%s). Falling back to LocalArtifactStore.", type(e).__name__)
                self._client = None
                self._bucket = None
            else:
                raise

    def save_artifact(self, descriptor: ArtifactDescriptor, content: bytes) -> ArtifactDescriptor:
        key = self.descriptor_key(descriptor)
        self._fallback_store.save_artifact(descriptor, content)
        if self._bucket is not None:
            try:
                blob = self._bucket.blob(key.object_path)
                blob.metadata = {
                    "tenant_id": key.tenant_id,
                    "scope": key.scope,
                    "scope_id": key.scope_id,
                    "kind": key.kind,
                    "category": descriptor.category.value,
                    "filename": sanitize_filename(descriptor.filename),
                }
                blob.upload_from_string(content, content_type=descriptor.content_type)
                descriptor.storage_uri = f"gs://{self.bucket_name}/{key.object_path}"
            except Exception as e:
                logger.error("GCS error in save_artifact: %s", type(e).__name__)
                if not self.fallback_on_error:
                    raise
        return descriptor

    def _candidate_paths(self, key: ArtifactKey) -> list[str]:
        paths = [key.object_path]
        if self.legacy_readable_by(key):
            paths.append(key.legacy_object_path)
        return paths

    def get_artifact_content(self, key: ArtifactKey) -> bytes | None:
        if self._bucket is not None:
            try:
                for path in self._candidate_paths(key):
                    blob = self._bucket.blob(path)
                    if blob.exists():
                        return blob.download_as_bytes()
            except Exception as e:
                logger.error("GCS error in get_artifact_content: %s", type(e).__name__)
                if not self.fallback_on_error:
                    raise
        return self._fallback_store.get_artifact_content(key)

    def get_descriptor(self, key: ArtifactKey) -> ArtifactDescriptor | None:
        cached = self._fallback_store.get_descriptor(key)
        if cached is not None:
            return cached
        if self._bucket is not None:
            try:
                for path in self._candidate_paths(key):
                    blob = self._bucket.get_blob(path)
                    if blob is None:
                        continue
                    meta = blob.metadata or {}
                    is_legacy = path == key.legacy_object_path
                    return ArtifactDescriptor(
                        artifact_id=key.artifact_id,
                        tenant_id=None if is_legacy else key.tenant_id,
                        scope=key.scope,
                        scope_id=key.scope_id,
                        kind=key.kind,
                        category=ArtifactCategory(meta.get("category", ArtifactCategory.SOURCE_JSON.value)),
                        filename=sanitize_filename(meta.get("filename", key.artifact_id)),
                        content_type=blob.content_type or "application/octet-stream",
                        size_bytes=int(blob.size or 0),
                        storage_uri=f"gs://{self.bucket_name}/{path}",
                    )
            except Exception as e:
                logger.error("GCS error in get_descriptor: %s", type(e).__name__)
                if not self.fallback_on_error:
                    raise
        return None

    def exists(self, key: ArtifactKey) -> bool:
        return self.get_descriptor(key) is not None
