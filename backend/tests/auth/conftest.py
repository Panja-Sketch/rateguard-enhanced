"""Fixtures for real-Firebase-verifier tests.

Two levels of fake:

* `FakeTokenVerifier` (dependency-injected) for route/role/tenant tests, so
  they need no Firebase at all.
* `signed_token` / `patch_google_certs` for tests of `FirebaseTokenVerifier`
  itself: real RS256 JWTs signed with a throwaway key, verified by the real
  firebase-admin code, with only Google's public-certificate download replaced.
"""

import datetime
import time
from collections.abc import Callable, Generator
from typing import Any

import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from app.auth import Role, UserRecord, get_token_verifier, get_user_directory
from app.auth.directory import InMemoryUserDirectory
from app.auth.verifier import (
    FirebaseTokenVerifier,
    TokenServiceUnavailableError,
    TokenVerificationError,
    VerifiedToken,
)
from app.main import app
from app.ratelimit import InMemoryRateLimiter, get_rate_limiter

PROJECT_ID = "rateguard-test"
KID = "test-key-1"

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"


class FakeTokenVerifier:
    """token string -> VerifiedToken; anything else is invalid. No network."""

    def __init__(self) -> None:
        self.tokens: dict[str, VerifiedToken] = {}
        self.unavailable = False

    def add(self, token: str, uid: str, email: str | None = None) -> None:
        self.tokens[token] = VerifiedToken(uid=uid, email=email)

    def verify(self, id_token: str) -> VerifiedToken:
        if self.unavailable:
            raise TokenServiceUnavailableError("down")
        if id_token == "expired-token":
            raise TokenVerificationError("expired")
        try:
            return self.tokens[id_token]
        except KeyError:
            raise TokenVerificationError("invalid") from None


ROLE_USERS = {
    Role.ADMIN: ("uid-admin", "admin-token"),
    Role.RELEASE_OWNER: ("uid-owner", "owner-token"),
    Role.CONSUMER_REVIEWER: ("uid-reviewer", "reviewer-token"),
    Role.VIEWER: ("uid-viewer", "viewer-token"),
}


@pytest.fixture
def auth_env() -> Generator[dict[str, Any], None, None]:
    """Real authentication dependency chain with an injected fake verifier and
    an in-memory user directory holding one user per role in tenant A, plus a
    tenant-B admin/owner/reviewer. Use with `@pytest.mark.real_auth`."""
    verifier = FakeTokenVerifier()
    directory = InMemoryUserDirectory()
    for role, (uid, token) in ROLE_USERS.items():
        verifier.add(token, uid, f"{uid}@example.test")
        directory.put_user(UserRecord(uid=uid, tenant_id=TENANT_A, role=role))
    for role, uid, token in (
        (Role.ADMIN, "uid-b-admin", "b-admin-token"),
        (Role.RELEASE_OWNER, "uid-b-owner", "b-owner-token"),
        (Role.CONSUMER_REVIEWER, "uid-b-reviewer", "b-reviewer-token"),
    ):
        verifier.add(token, uid)
        directory.put_user(UserRecord(uid=uid, tenant_id=TENANT_B, role=role))
    verifier.add("stranger-token", "uid-stranger")  # valid token, no user record
    verifier.add("disabled-token", "uid-disabled")
    directory.put_user(UserRecord(uid="uid-disabled", tenant_id=TENANT_A, role=Role.ADMIN, disabled=True))

    app.dependency_overrides[get_token_verifier] = lambda: verifier
    app.dependency_overrides[get_user_directory] = lambda: directory
    limiter = InMemoryRateLimiter()  # real limiter, real policies, fresh per test
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    yield {"verifier": verifier, "directory": directory, "limiter": limiter}
    app.dependency_overrides.pop(get_rate_limiter, None)
    app.dependency_overrides.pop(get_token_verifier, None)
    app.dependency_overrides.pop(get_user_directory, None)


@pytest.fixture
def api(auth_env: dict[str, Any]) -> Generator[TestClient, None, None]:
    from app.storage import reset_run_store

    reset_run_store()
    with TestClient(app) as c:
        yield c
    reset_run_store()


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---- real-JWT helpers for FirebaseTokenVerifier tests ----------------------


@pytest.fixture(scope="module")
def signing_material() -> dict[str, Any]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return {
        "key": key,
        "private_pem": key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ),
        "cert_pem": cert.public_bytes(serialization.Encoding.PEM).decode(),
    }


@pytest.fixture
def patch_google_certs(monkeypatch: pytest.MonkeyPatch, signing_material: dict[str, Any]) -> None:
    monkeypatch.setattr(
        "google.oauth2.id_token._fetch_certs",
        lambda request, certs_url: {KID: signing_material["cert_pem"]},
    )


@pytest.fixture
def signed_token(signing_material: dict[str, Any]) -> Callable[..., str]:
    def _make(**overrides: Any) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": f"https://securetoken.google.com/{PROJECT_ID}",
            "aud": PROJECT_ID,
            "sub": "uid-from-token",
            "iat": now - 10,
            "auth_time": now - 10,
            "exp": now + 3600,
            "email": "someone@example.test",
        }
        claims.update({k: v for k, v in overrides.items() if k != "_key"})
        key = overrides.get("_key", signing_material["private_pem"])
        return jwt.encode(claims, key, algorithm="RS256", headers={"kid": KID})

    return _make


@pytest.fixture
def firebase_verifier() -> FirebaseTokenVerifier:
    return FirebaseTokenVerifier(PROJECT_ID, app_name="rateguard-test-verifier")
