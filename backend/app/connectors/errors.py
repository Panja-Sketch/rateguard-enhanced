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

    def to_error_response(self) -> dict:
        """The locked doc section 13.5 wire shape: `{"error": {...}}`."""
        return {"error": self.error.model_dump(mode="json")}
