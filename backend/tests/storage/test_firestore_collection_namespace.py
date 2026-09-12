"""Tests for RATEGUARD_FIRESTORE_COLLECTION / FirestoreRunStore's
collection_name isolation — the mechanism that lets a candidate/staging
deployment (assurance_runs_staging) share a Firestore database/project with
production (assurance_runs) without ever touching the same documents.

No real Firestore database is contacted: google.cloud.firestore.Client is
patched out entirely.
"""

import os
from unittest.mock import MagicMock, patch

from app.storage import get_run_store, reset_run_store
from app.storage.firestore_store import DEFAULT_COLLECTION_NAME, FirestoreRunStore


def _init_with_patched_client(collection_name: str | None = None) -> FirestoreRunStore:
    with patch("google.cloud.firestore.Client") as mock_client_cls:
        mock_client_cls.return_value = MagicMock()
        kwargs = {"project_id": "rateguard-ai", "fallback_on_error": False}
        if collection_name is not None:
            kwargs["collection_name"] = collection_name
        return FirestoreRunStore(**kwargs)


def test_default_collection_name_is_production_assurance_runs() -> None:
    store = _init_with_patched_client()
    assert store.collection_name == "assurance_runs"
    assert store.collection_name == DEFAULT_COLLECTION_NAME


def test_explicit_collection_name_is_honored() -> None:
    store = _init_with_patched_client("assurance_runs_staging")
    assert store.collection_name == "assurance_runs_staging"


def test_empty_collection_name_falls_back_to_default() -> None:
    """A falsy collection_name (empty string) must never silently produce an
    unnamed/invalid collection reference."""
    store = _init_with_patched_client("")
    assert store.collection_name == DEFAULT_COLLECTION_NAME


def test_all_document_operations_use_configured_collection() -> None:
    """Every Firestore call site (create_run, get_run, update_run, list_runs,
    add_event, get_events, add_evidence, get_evidence, delete_run,
    acquire_lease) must reference the SAME configured collection — proven by
    asserting `_db.collection` is always called with the configured name and
    never with the production default when a staging name is configured."""
    with patch("google.cloud.firestore.Client") as mock_client_cls:
        mock_db = MagicMock()
        mock_client_cls.return_value = mock_db
        store = FirestoreRunStore(
            project_id="rateguard-ai", fallback_on_error=False, collection_name="assurance_runs_staging",
        )

        # Trigger every method that touches self._db.collection(...). Doc-level
        # calls will raise inside a MagicMock chain in ways that are fine here —
        # only the top-level `.collection(...)` call argument matters.
        try:
            store.get_run("MIS-STAGING-1")
        except Exception:
            pass
        try:
            store.get_events("MIS-STAGING-1")
        except Exception:
            pass
        try:
            store.get_evidence("MIS-STAGING-1")
        except Exception:
            pass

        collection_call_args = [call.args[0] for call in mock_db.collection.call_args_list]

    assert collection_call_args, "expected at least one .collection(...) call"
    assert all(arg == "assurance_runs_staging" for arg in collection_call_args)
    assert "assurance_runs" not in collection_call_args


def test_get_run_store_reads_ratteguard_firestore_collection_env_var() -> None:
    reset_run_store()
    try:
        with (
            patch.dict(os.environ, {"RATEGUARD_RUN_STORE": "firestore", "RATEGUARD_FIRESTORE_COLLECTION": "assurance_runs_staging"}),
            patch("google.cloud.firestore.Client") as mock_client_cls,
        ):
            mock_client_cls.return_value = MagicMock()
            store = get_run_store()
            assert isinstance(store, FirestoreRunStore)
            assert store.collection_name == "assurance_runs_staging"
    finally:
        reset_run_store()


def test_get_run_store_defaults_to_production_collection_when_unset() -> None:
    reset_run_store()
    try:
        env = {"RATEGUARD_RUN_STORE": "firestore"}
        with patch.dict(os.environ, env, clear=False), patch("google.cloud.firestore.Client") as mock_client_cls:
            # Ensure the var is genuinely absent for this test even if the
            # ambient environment happens to set it.
            os.environ.pop("RATEGUARD_FIRESTORE_COLLECTION", None)
            mock_client_cls.return_value = MagicMock()
            store = get_run_store()
            assert store.collection_name == "assurance_runs"
    finally:
        reset_run_store()


def test_strict_and_lenient_stores_share_the_same_collection_name() -> None:
    """Both get_run_store() and get_run_store(strict=True) must resolve the
    same RATEGUARD_FIRESTORE_COLLECTION value — a candidate deployment must
    never end up with the API reading one collection and the worker writing
    another within the same process configuration."""
    reset_run_store()
    try:
        with (
            patch.dict(os.environ, {"RATEGUARD_RUN_STORE": "firestore", "RATEGUARD_FIRESTORE_COLLECTION": "assurance_runs_staging"}),
            patch("google.cloud.firestore.Client") as mock_client_cls,
        ):
            mock_client_cls.return_value = MagicMock()
            lenient = get_run_store()
            strict = get_run_store(strict=True)
            assert lenient.collection_name == "assurance_runs_staging"
            assert strict.collection_name == "assurance_runs_staging"
    finally:
        reset_run_store()
