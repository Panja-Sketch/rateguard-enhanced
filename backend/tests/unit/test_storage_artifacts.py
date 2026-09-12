from app.storage.artifacts import (
    ArtifactCategory,
    ArtifactDescriptor,
    LocalArtifactStore,
    get_artifact_store,
)


def test_local_artifact_store_crud(tmp_path):
    """Tests saving, getting, listing, and deleting artifacts in LocalArtifactStore."""
    store = LocalArtifactStore(base_dir=tmp_path / "artifacts")

    descriptor = ArtifactDescriptor(
        artifact_id="ART-001",
        category=ArtifactCategory.SOURCE_JSON,
        filename="rate_spec.json",
        content_type="application/json",
        size_bytes=100,
        storage_uri="",
    )

    content = b'{"test": "data"}'
    saved_desc = store.save_artifact(descriptor, content)

    assert len(saved_desc.storage_uri) > 0
    assert store.get_artifact("ART-001") is not None
    assert store.get_artifact_content("ART-001") == content

    artifacts = store.list_artifacts()
    assert len(artifacts) == 1

    artifacts_cat = store.list_artifacts(category=ArtifactCategory.SOURCE_JSON)
    assert len(artifacts_cat) == 1

    assert store.delete_artifact("ART-001") is True
    assert store.get_artifact("ART-001") is None


def test_factory_returns_store():
    """Verifies factory returns a configured artifact store."""
    store = get_artifact_store()
    assert store is not None
