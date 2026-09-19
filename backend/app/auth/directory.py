"""Server-controlled user directory: `users/{firebase_uid}` in Firestore.

No API endpoint writes this collection, and Firestore security rules should
deny all client access to it; only the admin bootstrap script
(scripts/bootstrap_user.py) writes it today."""

import logging
import os
import threading
from abc import ABC, abstractmethod
from typing import Any

from app.auth.models import Role, UserRecord

logger = logging.getLogger(__name__)

USERS_COLLECTION = "users"


class UserDirectory(ABC):
    @abstractmethod
    def get_user(self, uid: str) -> UserRecord | None: ...

    @abstractmethod
    def put_user(self, record: UserRecord) -> None: ...


class InMemoryUserDirectory(UserDirectory):
    def __init__(self, users: list[UserRecord] | None = None) -> None:
        self._users: dict[str, UserRecord] = {u.uid: u for u in users or []}

    def get_user(self, uid: str) -> UserRecord | None:
        return self._users.get(uid)

    def put_user(self, record: UserRecord) -> None:
        self._users[record.uid] = record


def parse_user_document(uid: str, data: dict[str, Any] | None) -> UserRecord | None:
    """Fail-closed parse: a malformed document or unknown role means 'no access'."""
    if not data:
        return None
    try:
        tenant = data.get("tenant_id")
        if not isinstance(tenant, str) or not tenant.strip():
            return None
        return UserRecord(
            uid=uid,
            tenant_id=tenant.strip(),
            role=Role(data.get("role")),
            disabled=bool(data.get("disabled", False)),
            email=data.get("email") if isinstance(data.get("email"), str) else None,
        )
    except (ValueError, TypeError):
        logger.warning("AUTH_USER_RECORD_MALFORMED")
        return None


class FirestoreUserDirectory(UserDirectory):
    def __init__(self, project_id: str, database_id: str | None = None, collection: str = USERS_COLLECTION) -> None:
        self._project_id = project_id
        self._database_id = database_id
        self._collection = collection
        self._db: Any = None
        self._lock = threading.Lock()

    def _client(self) -> Any:
        if self._db is None:
            with self._lock:
                if self._db is None:
                    from google.cloud import firestore

                    kwargs: dict[str, Any] = {"project": self._project_id}
                    if self._database_id and self._database_id != "(default)":
                        kwargs["database"] = self._database_id
                    self._db = firestore.Client(**kwargs)
        return self._db

    def get_user(self, uid: str) -> UserRecord | None:
        snap = self._client().collection(self._collection).document(uid).get()
        return parse_user_document(uid, snap.to_dict() if snap.exists else None)

    def put_user(self, record: UserRecord) -> None:
        self._client().collection(self._collection).document(record.uid).set(
            {
                "role": record.role.value,
                "tenant_id": record.tenant_id,
                "disabled": record.disabled,
                "email": record.email,
            },
            merge=True,
        )


def build_user_directory(project_id: str) -> UserDirectory:
    """Firestore when the run store is Firestore (deployed); otherwise an empty
    in-memory directory, so a local API without Firestore admits nobody until a
    test or developer injects users explicitly."""
    if os.getenv("RATEGUARD_RUN_STORE", "memory").lower() == "firestore":
        return FirestoreUserDirectory(project_id, os.getenv("RATEGUARD_FIRESTORE_DATABASE") or None)
    return InMemoryUserDirectory()
