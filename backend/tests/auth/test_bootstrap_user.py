"""Bootstrap tooling: idempotent, explicit, no credentials, no Firebase calls in tests."""

import importlib.util
from pathlib import Path

import pytest

from app.auth.directory import InMemoryUserDirectory
from app.auth.models import Role, UserRecord

_SPEC = importlib.util.spec_from_file_location(
    "bootstrap_user", Path(__file__).resolve().parents[2] / "scripts" / "bootstrap_user.py"
)
bootstrap = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bootstrap)


def _run(directory, **kw):
    args = dict(
        uid="u1",
        email="demo@example.test",
        tenant_id="rateguard-demo",
        role=Role.ADMIN,
        firebase_disabled=False,
        allow_tenant_change=False,
        apply=True,
    )
    args.update(kw)
    return bootstrap.run_assignment(directory, **args)


def test_creates_then_is_idempotent():
    d = InMemoryUserDirectory()
    assert _run(d) == ("create", None)
    assert d.get_user("u1").role == Role.ADMIN and d.get_user("u1").tenant_id == "rateguard-demo"
    assert _run(d) == ("unchanged", None)


def test_dry_run_writes_nothing():
    d = InMemoryUserDirectory()
    assert _run(d, apply=False) == ("create", None)
    assert d.get_user("u1") is None


def test_role_change_updates_in_place():
    d = InMemoryUserDirectory([UserRecord(uid="u1", tenant_id="rateguard-demo", role=Role.VIEWER)])
    assert _run(d, role=Role.ADMIN) == ("update", None)
    assert d.get_user("u1").role == Role.ADMIN


def test_tenant_change_requires_explicit_flag():
    d = InMemoryUserDirectory([UserRecord(uid="u1", tenant_id="other", role=Role.ADMIN)])
    action, reason = _run(d)
    assert action == "refuse" and "tenant" in reason
    assert d.get_user("u1").tenant_id == "other"
    assert _run(d, allow_tenant_change=True) == ("update", None)


def test_refuses_disabled_firebase_account():
    d = InMemoryUserDirectory()
    action, reason = _run(d, firebase_disabled=True)
    assert action == "refuse" and d.get_user("u1") is None


@pytest.mark.parametrize("tenant", ["rateguard-demo", "a", "tenant_1"])
def test_valid_tenant_ids(tenant):
    assert bootstrap.valid_tenant_id(tenant)


@pytest.mark.parametrize("tenant", ["", "Has Space", "UPPER", "../x", "a" * 65, "-lead"])
def test_invalid_tenant_ids(tenant):
    assert not bootstrap.valid_tenant_id(tenant)


def test_requires_explicit_project_id_and_matching_confirmation(capsys):
    with pytest.raises(SystemExit):
        bootstrap._parse_args(["--role", "ADMIN", "--email", "a@b.c"])  # no --project-id
    with pytest.raises(SystemExit):
        bootstrap._parse_args(["--project-id", "p", "--role", "ADMIN"])  # no --email/--uid
    with pytest.raises(SystemExit):
        bootstrap._parse_args(["--project-id", "p", "--role", "GOD", "--email", "a@b.c"])
    assert bootstrap.main(["--project-id", "p", "--role", "ADMIN", "--email", "a@b.c", "--confirm", "other"]) == 2


def test_script_contains_no_credential_material():
    text = (Path(bootstrap.__file__)).read_text(encoding="utf-8")
    for needle in ("BEGIN PRIVATE KEY", "AIza", "client_secret", "Certificate("):
        assert needle not in text
    assert "ApplicationDefault" in text
