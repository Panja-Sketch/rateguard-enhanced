from types import SimpleNamespace

from app.core.config import get_settings
from app.storage.artifacts import ArtifactCategory, ArtifactDescriptor, ArtifactKey
from app.storage.artifacts.gcs_store import GCSArtifactStore


class _FakeBlob:
    def __init__(self, bucket_data: dict, path: str) -> None:
        self._bucket_data = bucket_data
        self._path = path
        self.metadata: dict | None = None
        self.content_type: str | None = None

    def upload_from_string(self, content: bytes, content_type: str | None = None) -> None:
        self._bucket_data[self._path] = (content, dict(self.metadata or {}), content_type)

    def exists(self) -> bool:
        return self._path in self._bucket_data

    def download_as_bytes(self) -> bytes:
        return self._bucket_data[self._path][0]


class _FakeBucket:
    """Shared in-memory stand-in for a real GCS bucket, so two independent
    GCSArtifactStore instances (simulating two separate Cloud Run
    services/processes, e.g. the API and the worker) can prove they see
    each other's writes."""

    def __init__(self) -> None:
        self._data: dict[str, tuple] = {}

    def blob(self, path: str) -> _FakeBlob:
        return _FakeBlob(self._data, path)

    def get_blob(self, path: str):
        if path not in self._data:
            return None
        content, meta, ctype = self._data[path]
        return SimpleNamespace(metadata=meta, content_type=ctype, size=len(content))


def _store_with_fake_bucket(fake_bucket: _FakeBucket) -> GCSArtifactStore:
    store = GCSArtifactStore(fallback_on_error=True)
    store._bucket = fake_bucket
    return store


def _compiled_descriptor(tenant: str = "tenant-a") -> ArtifactDescriptor:
    return ArtifactDescriptor(
        artifact_id="IPIR-SRC-TEST1234",
        tenant_id=tenant,
        scope="sources",
        scope_id="SRC-TEST1234",
        kind="compiled",
        category=ArtifactCategory.IPIR_PACKAGE,
        filename="some_package.json",
        content_type="application/json",
        size_bytes=12,
        storage_uri="",
    )


def _key(tenant: str = "tenant-a") -> ArtifactKey:
    return ArtifactKey(tenant, "sources", "SRC-TEST1234", "compiled", "IPIR-SRC-TEST1234")


def test_get_artifact_content_visible_across_independent_store_instances():
    """Regression test for the real production failure: the API compiles a
    source and saves the IPIR artifact; the worker (a separate Cloud Run
    service/process with its own empty local descriptor cache) must still be
    able to read it back from the shared bucket."""
    fake_bucket = _FakeBucket()
    api_side_store = _store_with_fake_bucket(fake_bucket)
    worker_side_store = _store_with_fake_bucket(fake_bucket)

    api_side_store.save_artifact(_compiled_descriptor(), b'{"ok": true}')

    # The worker's own process-local cache never saw it -- only the bucket did.
    assert worker_side_store._fallback_store.get_descriptor(_key()) is None
    assert worker_side_store.get_artifact_content(_key()) == b'{"ok": true}'
    descriptor = worker_side_store.get_descriptor(_key())
    assert descriptor is not None and descriptor.tenant_id == "tenant-a" and descriptor.filename == "some_package.json"


def test_objects_are_written_under_the_locked_tenant_prefix():
    bucket = _FakeBucket()
    _store_with_fake_bucket(bucket).save_artifact(_compiled_descriptor(), b"x")
    assert list(bucket._data) == ["tenants/tenant-a/sources/SRC-TEST1234/compiled/IPIR-SRC-TEST1234"]
    assert bucket._data["tenants/tenant-a/sources/SRC-TEST1234/compiled/IPIR-SRC-TEST1234"][1]["tenant_id"] == "tenant-a"


def test_another_tenant_cannot_read_exists_or_describe_the_object():
    bucket = _FakeBucket()
    _store_with_fake_bucket(bucket).save_artifact(_compiled_descriptor("tenant-a"), b"SECRET")
    other = _store_with_fake_bucket(bucket)  # fresh process, no local cache
    assert other.get_artifact_content(_key("tenant-b")) is None
    assert other.exists(_key("tenant-b")) is False
    assert other.get_descriptor(_key("tenant-b")) is None
    assert other.get_artifact_content(_key("tenant-a")) == b"SECRET"


def test_get_artifact_content_missing_returns_none():
    store = _store_with_fake_bucket(_FakeBucket())
    assert store.get_artifact_content(_key()) is None


def test_legacy_tenantless_objects_are_not_visible_to_every_tenant(monkeypatch):
    bucket = _FakeBucket()
    bucket._data["artifacts/IPIR-SRC-TEST1234"] = (b"LEGACY", {}, "application/json")
    store = _store_with_fake_bucket(bucket)

    monkeypatch.setattr(get_settings(), "legacy_record_tenant_id", None)
    assert store.get_artifact_content(_key("tenant-a")) is None
    assert store.get_artifact_content(_key("tenant-b")) is None

    # Explicit server-side assignment exposes them to that one tenant only.
    monkeypatch.setattr(get_settings(), "legacy_record_tenant_id", "tenant-a")
    assert store.get_artifact_content(_key("tenant-a")) == b"LEGACY"
    assert store.get_artifact_content(_key("tenant-b")) is None
    # Evidence is never legacy-readable.
    ev = ArtifactKey("tenant-a", "missions", "MIS-1", "evidence", "IPIR-SRC-TEST1234")
    assert store.get_artifact_content(ev) is None
