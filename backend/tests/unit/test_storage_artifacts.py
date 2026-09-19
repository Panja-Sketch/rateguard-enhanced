import pytest

from app.storage.artifacts import (
    ArtifactCategory,
    ArtifactDescriptor,
    ArtifactKey,
    ArtifactPathError,
    LocalArtifactStore,
    get_artifact_store,
)


def _descriptor(tenant: str = "tenant-a", artifact_id: str = "ART-001", filename: str = "rate_spec.json") -> ArtifactDescriptor:
    return ArtifactDescriptor(
        artifact_id=artifact_id,
        tenant_id=tenant,
        scope="sources",
        scope_id="SRC-1",
        kind="raw",
        category=ArtifactCategory.SOURCE_JSON,
        filename=filename,
        content_type="application/json",
        size_bytes=100,
        storage_uri="",
    )


def _key(tenant: str = "tenant-a", artifact_id: str = "ART-001") -> ArtifactKey:
    return ArtifactKey(tenant, "sources", "SRC-1", "raw", artifact_id)


def test_local_artifact_store_crud(tmp_path):
    """Save, get, list, and delete artifacts in LocalArtifactStore (tenant-scoped)."""
    store = LocalArtifactStore(base_dir=tmp_path / "artifacts")
    content = b'{"test": "data"}'
    saved = store.save_artifact(_descriptor(), content)

    assert len(saved.storage_uri) > 0
    assert "tenants/tenant-a/sources/SRC-1/raw/ART-001".replace("/", "\\") in saved.storage_uri or (
        "tenants/tenant-a/sources/SRC-1/raw/ART-001" in saved.storage_uri
    )
    assert store.get_artifact(_key()) is not None
    assert store.get_artifact_content(_key()) == content
    assert len(store.list_artifacts("tenant-a")) == 1
    assert len(store.list_artifacts("tenant-a", category=ArtifactCategory.SOURCE_JSON)) == 1
    assert store.list_artifacts("tenant-b") == []
    assert store.delete_artifact(_key()) is True
    assert store.get_artifact(_key()) is None


def test_local_store_isolates_tenants_for_read_exists_descriptor_and_delete(tmp_path):
    store = LocalArtifactStore(base_dir=tmp_path / "artifacts")
    store.save_artifact(_descriptor("tenant-a"), b"A-DATA")
    store.save_artifact(_descriptor("tenant-b"), b"B-DATA")

    assert store.get_artifact_content(_key("tenant-a")) == b"A-DATA"
    assert store.get_artifact_content(_key("tenant-b")) == b"B-DATA"
    assert store.get_artifact_content(_key("tenant-c")) is None
    assert store.exists(_key("tenant-c")) is False
    assert store.get_descriptor(_key("tenant-c")) is None
    assert store.delete_artifact(_key("tenant-c")) is False
    assert store.get_artifact_content(_key("tenant-a")) == b"A-DATA"


def test_local_store_refuses_tenantless_or_incomplete_descriptors(tmp_path):
    store = LocalArtifactStore(base_dir=tmp_path / "artifacts")
    bad = _descriptor()
    bad.tenant_id = None
    with pytest.raises(ArtifactPathError):
        store.save_artifact(bad, b"x")


def test_original_filename_never_influences_the_path(tmp_path):
    store = LocalArtifactStore(base_dir=tmp_path / "artifacts")
    saved = store.save_artifact(_descriptor(filename="../../../evil name.json"), b"x")
    assert saved.filename == "evil name.json"
    assert not (tmp_path / "evil name.json").exists()
    assert str(tmp_path / "artifacts") in saved.storage_uri


def test_factory_returns_store():
    """Verifies factory returns a configured artifact store."""
    assert get_artifact_store() is not None


# ---- path validation --------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    ["", "..", ".", "a/b", "a\\b", "../x", "a..b/c", "%2e%2e", "%2e%2e%2fetc", "a%2fb", "a b", "a\x00b", "a\nb", "-lead", "x" * 129, "é", "a:b"],
)
def test_unsafe_identifiers_are_rejected_in_every_position(bad):
    with pytest.raises(ArtifactPathError):
        ArtifactKey(bad, "sources", "SRC-1", "raw", "ART-1")
    with pytest.raises(ArtifactPathError):
        ArtifactKey("tenant-a", "sources", bad, "raw", "ART-1")
    with pytest.raises(ArtifactPathError):
        ArtifactKey("tenant-a", "sources", "SRC-1", "raw", bad)


@pytest.mark.parametrize(
    ("scope", "kind"),
    [("sources", "evidence"), ("missions", "raw"), ("missions", "compiled"), ("tenants", "raw"), ("sources", "../raw"), ("", "")],
)
def test_scope_and_kind_are_a_closed_set(scope, kind):
    with pytest.raises(ArtifactPathError):
        ArtifactKey("tenant-a", scope, "SRC-1", kind, "ART-1")


def test_locked_layout_is_exact():
    assert ArtifactKey("t1", "sources", "SRC-1", "raw", "SRC-1").object_path == "tenants/t1/sources/SRC-1/raw/SRC-1"
    assert (
        ArtifactKey("t1", "sources", "SRC-1", "compiled", "IPIR-SRC-1").object_path
        == "tenants/t1/sources/SRC-1/compiled/IPIR-SRC-1"
    )
    assert (
        ArtifactKey("t1", "missions", "MIS-1", "evidence", "EVB-abc").object_path
        == "tenants/t1/missions/MIS-1/evidence/EVB-abc"
    )
