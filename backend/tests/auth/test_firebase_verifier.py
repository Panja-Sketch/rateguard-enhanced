"""`FirebaseTokenVerifier` against real RS256 JWTs and the real firebase-admin
verification code. Only Google's public-certificate download is replaced."""

import time

import pytest

from app.auth.verifier import TokenServiceUnavailableError, TokenVerificationError
from tests.auth.conftest import PROJECT_ID

pytestmark = pytest.mark.usefixtures("patch_google_certs")


def test_valid_token_is_verified(firebase_verifier, signed_token):
    verified = firebase_verifier.verify(signed_token())
    assert verified.uid == "uid-from-token"
    assert verified.email == "someone@example.test"


def test_expired_token_rejected(firebase_verifier, signed_token):
    now = int(time.time())
    with pytest.raises(TokenVerificationError) as exc:
        firebase_verifier.verify(signed_token(iat=now - 7200, auth_time=now - 7200, exp=now - 3600))
    assert exc.value.reason == "expired"


def test_wrong_audience_rejected(firebase_verifier, signed_token):
    with pytest.raises(TokenVerificationError):
        firebase_verifier.verify(signed_token(aud="some-other-project"))


def test_wrong_issuer_rejected(firebase_verifier, signed_token):
    with pytest.raises(TokenVerificationError):
        firebase_verifier.verify(signed_token(iss="https://securetoken.google.com/some-other-project"))


def test_attacker_issuer_rejected(firebase_verifier, signed_token):
    with pytest.raises(TokenVerificationError):
        firebase_verifier.verify(signed_token(iss="https://evil.example.test/"))


def test_bad_signature_rejected(firebase_verifier, signed_token):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    other = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    with pytest.raises(TokenVerificationError):
        firebase_verifier.verify(signed_token(_key=other))


def test_missing_subject_rejected(firebase_verifier, signed_token):
    with pytest.raises(TokenVerificationError):
        firebase_verifier.verify(signed_token(sub=""))


def test_overlong_subject_rejected(firebase_verifier, signed_token):
    with pytest.raises(TokenVerificationError):
        firebase_verifier.verify(signed_token(sub="x" * 129))


def test_garbage_token_rejected(firebase_verifier):
    with pytest.raises(TokenVerificationError):
        firebase_verifier.verify("not-a-jwt")


def test_verifier_requires_project_id():
    from app.auth.verifier import FirebaseTokenVerifier

    with pytest.raises(ValueError):
        FirebaseTokenVerifier("")


def test_certificate_fetch_failure_is_unavailable_not_invalid(firebase_verifier, signed_token, monkeypatch):
    import google.auth.exceptions

    def boom(request, certs_url):
        raise google.auth.exceptions.TransportError("network down")

    monkeypatch.setattr("google.oauth2.id_token._fetch_certs", boom)
    with pytest.raises(TokenServiceUnavailableError):
        firebase_verifier.verify(signed_token())


def test_verifier_needs_no_credentials_file(firebase_verifier, signed_token, monkeypatch):
    """Verification must work with no service-account JSON and no key secret."""
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.delenv("FIREBASE_ADMIN_KEY_SECRET", raising=False)
    assert firebase_verifier.verify(signed_token()).uid == "uid-from-token"
    assert PROJECT_ID == "rateguard-test"
