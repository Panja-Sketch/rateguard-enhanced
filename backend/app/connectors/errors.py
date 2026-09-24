"""Typed failure model for the REST rating-engine connector.

Matches the locked doc section 13.5 error shape exactly:

    {"error": {"code", "message", "correlation_id", "details"}}

and carries the locked doc section 16.2 three-way retry classification
(RETRYABLE / NON_RETRYABLE / REVIEW_REQUIRED) on every single failure, so a
caller (a future mission pipeline) never has to guess a category from an
exception class name alone. This mirrors the `WorkbookError` /
`WorkbookRejectionError` convention already established by CP7
(`backend/app/ingestion/workbook_v1/{receipt,errors}.py`) — one consistent
error-shape convention across the codebase, not two.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ConnectorFailureCategory(StrEnum):
    """Locked doc section 16.2 retry classification."""

    RETRYABLE = "RETRYABLE"
    NON_RETRYABLE = "NON_RETRYABLE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class ConnectorFailureClass(StrEnum):
    """Operator-facing failure taxonomy. Every connector failure code maps to
    exactly one class, so an authentication problem, a timeout, a contract
    violation, an unsupported version and a plain outage stay distinguishable
    without exposing any detail (endpoint, token, payload) beyond the class."""

    AUTH_DENIED = "CONNECTOR_AUTH_DENIED"
    TIMEOUT = "CONNECTOR_TIMEOUT"
    CONTRACT_ERROR = "CONNECTOR_CONTRACT_ERROR"
    VERSION_UNSUPPORTED = "CONNECTOR_VERSION_UNSUPPORTED"
    UNAVAILABLE = "CONNECTOR_UNAVAILABLE"


_FAILURE_CLASS_BY_CODE: dict[str, ConnectorFailureClass] = {
    "CONNECTOR_AUTH_DENIED": ConnectorFailureClass.AUTH_DENIED,
    "CONNECTOR_AUTH_UNAVAILABLE": ConnectorFailureClass.AUTH_DENIED,
    "CONNECTOR_TIMEOUT": ConnectorFailureClass.TIMEOUT,
    "CONNECTOR_ENGINE_VERSION_NOT_ALLOWED": ConnectorFailureClass.VERSION_UNSUPPORTED,
    "CONNECTOR_ENGINE_VERSION_MISMATCH": ConnectorFailureClass.VERSION_UNSUPPORTED,
    "CONNECTOR_NOT_REGISTERED": ConnectorFailureClass.CONTRACT_ERROR,
    "CONNECTOR_MALFORMED_JSON": ConnectorFailureClass.CONTRACT_ERROR,
    "CONNECTOR_SCHEMA_VIOLATION": ConnectorFailureClass.CONTRACT_ERROR,
    "CONNECTOR_UNSUPPORTED_TRACE_NODE": ConnectorFailureClass.CONTRACT_ERROR,
    "CONNECTOR_OUTPUT_NOT_DECIMAL_STRING": ConnectorFailureClass.CONTRACT_ERROR,
    "CONNECTOR_REQUEST_ID_MISMATCH": ConnectorFailureClass.CONTRACT_ERROR,
    "CONNECTOR_INCOMPLETE_OUTPUT_BATCH": ConnectorFailureClass.CONTRACT_ERROR,
    "CONNECTOR_BATCH_ITEM_MISMATCH": ConnectorFailureClass.CONTRACT_ERROR,
    "CONNECTOR_BATCH_REQUEST_TOO_LARGE": ConnectorFailureClass.CONTRACT_ERROR,
    "CONNECTOR_RESPONSE_TOO_LARGE": ConnectorFailureClass.CONTRACT_ERROR,
    "CONNECTOR_REQUEST_REJECTED": ConnectorFailureClass.CONTRACT_ERROR,
    "CONNECTOR_UNEXPECTED_STATUS": ConnectorFailureClass.CONTRACT_ERROR,
    "CONNECTOR_UNEXPECTED_REDIRECT": ConnectorFailureClass.CONTRACT_ERROR,
}


def classify_failure(code: str | None) -> ConnectorFailureClass:
    """Maps a connector error code to its failure class. Anything not known to
    be an authentication, timeout, contract or version problem is treated as
    the connector being unavailable (transport failure, 5xx, DNS, circuit
    open, budget exhausted, ...)."""
    return _FAILURE_CLASS_BY_CODE.get(code or "", ConnectorFailureClass.UNAVAILABLE)


class ConnectorError(BaseModel):
    """The locked doc section 13.5 error shape, plus the retry category."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    correlation_id: str
    details: list[dict] = Field(default_factory=list)
    category: ConnectorFailureCategory


class ConnectorException(Exception):
    """Raised for every connector failure. Always carries a typed
    `ConnectorError` — code, message, correlation_id, details, and a retry
    category — so a caller can classify the failure without inspecting the
    exception's Python class."""

    def __init__(
        self,
        code: str,
        message: str,
        category: ConnectorFailureCategory,
        *,
        correlation_id: str | None = None,
        details: list[dict] | None = None,
    ) -> None:
        super().__init__(message)
        self.error = ConnectorError(
            code=code,
            message=message,
            correlation_id=correlation_id or str(uuid.uuid4()),
            details=details or [],
            category=category,
        )

    @property
    def category(self) -> ConnectorFailureCategory:
        return self.error.category

    @property
    def failure_class(self) -> ConnectorFailureClass:
        return classify_failure(self.error.code)

    def to_error_response(self) -> dict:
        """The locked doc section 13.5 wire shape: `{"error": {...}}`."""
        return {"error": self.error.model_dump(mode="json")}
