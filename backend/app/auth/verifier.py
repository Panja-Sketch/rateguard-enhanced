"""Firebase ID-token verification (locked doc 15.2).

`FirebaseTokenVerifier` uses the Firebase Admin SDK with Application Default
Credentials only -- no service-account JSON and no Secret Manager key. The
SDK verifies signature (Google's rotating public certs), audience (project
id), issuer (`https://securetoken.google.com/<project>`), expiry, and a
non-empty subject; this class re-asserts audience/issuer/subject explicitly
as defense in depth and returns a minimal `VerifiedToken`.

Token *verification* needs no IAM at all (public certs). Optional revocation
checking (`check_revoked=True`) calls the Firebase Auth API and therefore needs
the runtime service account to hold `roles/firebaseauth.viewer`; it is off by
default and account disablement is instead enforced server-side through the
`users/{uid}.disabled` flag (see app.auth.directory).
"""

import logging
import threading
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class TokenVerificationError(Exception):
    """The token is invalid (bad signature, expired, wrong audience, ...).
    `reason` is a short internal label for logs -- never token content."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class TokenServiceUnavailableError(Exception):
    """Verification could not be completed (e.g. certificate fetch failed)."""


@dataclass(frozen=True)
class VerifiedToken:
    uid: str
    email: str | None = None


class TokenVerifier(Protocol):
    def verify(self, id_token: str) -> VerifiedToken: ...


_init_lock = threading.Lock()


class FirebaseTokenVerifier:
    def __init__(self, project_id: str, *, check_revoked: bool = False, app_name: str | None = None) -> None:
        if not project_id:
            raise ValueError("A Firebase project id is required for token verification.")
        self._project_id = project_id
        self._check_revoked = check_revoked
        self._app_name = app_name or f"rateguard-{project_id}"
        self._app: Any = None

    def _get_app(self) -> Any:
        if self._app is not None:
            return self._app
        with _init_lock:
            if self._app is not None:
                return self._app
            import firebase_admin
            from firebase_admin import credentials

            try:
                self._app = firebase_admin.get_app(self._app_name)
            except ValueError:
                # ADC only (attached service account on Cloud Run; the
                # developer's `gcloud auth application-default login` locally).
                self._app = firebase_admin.initialize_app(
                    credentials.ApplicationDefault(),
                    options={"projectId": self._project_id},
                    name=self._app_name,
                )
        return self._app

    def verify(self, id_token: str) -> VerifiedToken:
        from firebase_admin import auth
        from firebase_admin import exceptions as fb_exceptions

        try:
            claims = auth.verify_id_token(id_token, app=self._get_app(), check_revoked=self._check_revoked)
        except auth.ExpiredIdTokenError as exc:
            raise TokenVerificationError("expired") from exc
        except auth.RevokedIdTokenError as exc:
            raise TokenVerificationError("revoked") from exc
        except auth.UserDisabledError as exc:
            raise TokenVerificationError("user_disabled") from exc
        except auth.CertificateFetchError as exc:
            raise TokenServiceUnavailableError("certificate fetch failed") from exc
        except (auth.InvalidIdTokenError, ValueError) as exc:
            raise TokenVerificationError("invalid") from exc
        except fb_exceptions.FirebaseError as exc:
            raise TokenServiceUnavailableError("firebase error") from exc

        uid = claims.get("uid") or claims.get("sub")
        if not isinstance(uid, str) or not uid or len(uid) > 128:
            raise TokenVerificationError("bad_subject")
        if claims.get("aud") != self._project_id:
            raise TokenVerificationError("bad_audience")
        if claims.get("iss") != f"https://securetoken.google.com/{self._project_id}":
            raise TokenVerificationError("bad_issuer")
        email = claims.get("email")
        return VerifiedToken(uid=uid, email=email if isinstance(email, str) else None)
