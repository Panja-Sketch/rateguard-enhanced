"""Regression test for the historical "400 Invalid database id %28default%29"
incident.

Root cause (confirmed): a google-api-core==2.35.0 regression that
percent-encodes the literal, valid Firestore database id "(default)" before
it reaches the API. The real fix is the google-api-core==2.34.0 pin in
pyproject.toml — this test does NOT re-verify that upstream library behavior
(that would require a real network call). It verifies the narrower, local
claim: `FirestoreRunStore.__init__` never manually constructs or passes a
percent-encoded (or otherwise mangled) database id string to
`firestore.Client(...)`, and normalizes the common "unset" / "(default)"
cases to omitting the `database` kwarg entirely rather than passing a literal
"(default)" through unnecessarily.

No real Firestore database is contacted: `google.cloud.firestore.Client` is
patched out entirely.
"""

from unittest.mock import MagicMock, patch

from app.storage.firestore_store import FirestoreRunStore


def _init_with_patched_client(database_id: str | None) -> MagicMock:
    """Constructs a FirestoreRunStore with google.cloud.firestore.Client patched
    to a MagicMock, and returns that mock so its call args can be inspected."""
    with patch("google.cloud.firestore.Client") as mock_client_cls:
        mock_client_cls.return_value = MagicMock()
        FirestoreRunStore(project_id="rateguard-ai", database_id=database_id, fallback_on_error=False)
    return mock_client_cls


def test_unset_database_id_omits_database_kwarg() -> None:
    mock_client_cls = _init_with_patched_client(None)
    mock_client_cls.assert_called_once_with(project="rateguard-ai")
    assert "database" not in mock_client_cls.call_args.kwargs


def test_explicit_default_marker_omits_database_kwarg() -> None:
    """"(default)" is a valid Firestore database id, but this client
    normalizes it to "no explicit database kwarg" defensively — it must never
    be forwarded as a literal string that some layer could mangle."""
    mock_client_cls = _init_with_patched_client("(default)")
    mock_client_cls.assert_called_once_with(project="rateguard-ai")
    assert "database" not in mock_client_cls.call_args.kwargs


def test_named_database_id_is_passed_through_unmodified() -> None:
    mock_client_cls = _init_with_patched_client("my-named-db")
    mock_client_cls.assert_called_once_with(project="rateguard-ai", database="my-named-db")


def test_no_call_ever_contains_a_percent_encoded_database_value() -> None:
    """Defends against any future regression that re-introduces manual
    URL/percent-encoding of the database id before it reaches the client."""
    for database_id in (None, "(default)", "my-named-db"):
        mock_client_cls = _init_with_patched_client(database_id)
        passed_database = mock_client_cls.call_args.kwargs.get("database")
        if passed_database is not None:
            assert "%28" not in passed_database
            assert "%29" not in passed_database
            assert passed_database == database_id
