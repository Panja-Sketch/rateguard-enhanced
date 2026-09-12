from app.storage.artifacts.gcs_store import GCSArtifactStore
from app.storage.artifacts.models import ArtifactCategory, ArtifactDescriptor


class _FakeBlob:
    def __init__(self, bucket_data: dict, path: str) -> None:
        self._bucket_data = bucket_data
        self._path = path

    def upload_from_string(self, content: bytes, content_type: str | None = None) -> None:
        self._bucket_data[self._path] = content

    def exists(self) -> bool:
        return self._path in self._bucket_data

    def download_as_bytes(self) -> bytes:
        return self._bucket_data[self._path]


class _FakeBucket:
    """Shared in-memory stand-in for a real GCS bucket, so two independent
    GCSArtifactStore instances (simulating two separate Cloud Run
    services/processes, e.g. the API and the worker) can prove they see
    each other's writes."""

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    def blob(self, path: str) -> _FakeBlob:
        return _FakeBlob(self._data, path)


def _store_with_fake_bucket(fake_bucket: _FakeBucket) -> GCSArtifactStore:
    store = GCSArtifactStore(fallback_on_error=True)
    store._bucket = fake_bucket
    return store


def test_get_artifact_content_visible_across_independent_store_instances():
    """Regression test for the real production failure: the API compiles a
    source and saves the IPIR artifact; the worker (a separate Cloud Run
    service/process with its own empty local descriptor cache) must still be
    able to read it back from the shared bucket. Before the fix,
    get_artifact_content required the reading process's own local descriptor
    cache to already know the artifact, so the worker always got None and
    mis-treated the source_id as an unsupported demo package_id."""
    fake_bucket = _FakeBucket()
    api_side_store = _store_with_fake_bucket(fake_bucket)
    worker_side_store = _store_with_fake_bucket(fake_bucket)

    descriptor = ArtifactDescriptor(
        artifact_id="IPIR-SRC-TEST1234",
        category=ArtifactCategory.IPIR_PACKAGE,
        filename="some_package.json",
        content_type="application/json",
        size_bytes=12,
        storage_uri="",
    )
    api_side_store.save_artifact(descriptor, b'{"ok": true}')

    # The worker's own process-local descriptor cache never saw this
    # artifact_id -- only the shared GCS bucket did.
    assert worker_side_store.get_descriptor("IPIR-SRC-TEST1234") is None
    assert worker_side_store.get_artifact_content("IPIR-SRC-TEST1234") == b'{"ok": true}'


def test_get_artifact_content_missing_returns_none():
    fake_bucket = _FakeBucket()
    store = _store_with_fake_bucket(fake_bucket)
    assert store.get_artifact_content("does-not-exist") is None
