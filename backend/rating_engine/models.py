from datetime import UTC, date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.ipir.enums import TransactionType


class QuoteRequest(BaseModel):
    """Request contract (locked doc section 8.1). Unknown fields rejected."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    engine_version: str
    product_id: str
    effective_date: date
    transaction_type: TransactionType
    inputs: dict[str, Any]
    trace_requested: bool = False


class TraceStepOut(BaseModel):
    """A minimal, wire-safe trace step — deliberately not the full internal
    `PremiumTrace`/`Provenance` shape, to avoid leaking internal package
    structure over the wire by default."""

    model_config = ConfigDict(extra="forbid")

    node_id: str
    node_type: str
    operation: str
    result: str


class QuoteResponse(BaseModel):
    """Response contract (locked doc section 8.1)."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    engine_version: str
    outputs: dict[str, str]
    trace: list[TraceStepOut] = Field(default_factory=list)
    rated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class QuoteError(BaseModel):
    """Error contract, matching the locked section 13.5 error model shape."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    correlation_id: str
