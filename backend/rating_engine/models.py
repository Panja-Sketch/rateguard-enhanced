"""Wire models for the RateGuard Demo Insurer Rating Engine's REST contract.
Self-contained (no RateGuard import). Request models reject unknown fields, so a
caller cannot smuggle tenant identity or authorization claims into a quote."""

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TransactionType(StrEnum):
    """Policy transaction contexts on the wire (this plan rates the first two)."""

    NEW_BUSINESS = "NEW_BUSINESS"
    RENEWAL = "RENEWAL"
    POLICY_CHANGE = "POLICY_CHANGE"


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


# -- optional batch-quote capability (`quote-batch-v1`) ------------------------
# Advertised through `GET /capabilities`; the single-quote `/quote` contract
# above is unchanged and remains the baseline every connector must support.

BATCH_SCHEMA_VERSION = "quote-batch-v1"
BATCH_MAX_ITEMS = 250


class BatchQuoteItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    effective_date: date
    transaction_type: TransactionType
    inputs: dict[str, Any]


class BatchQuoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = BATCH_SCHEMA_VERSION
    batch_id: str
    engine_version: str
    product_id: str
    items: list[BatchQuoteItem] = Field(min_length=1, max_length=BATCH_MAX_ITEMS)


class BatchItemError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str = ""


class BatchQuoteItemResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    status: str
    outputs: dict[str, str] = Field(default_factory=dict)
    error: BatchItemError | None = None


class BatchQuoteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = BATCH_SCHEMA_VERSION
    batch_id: str
    engine_version: str
    results: list[BatchQuoteItemResult]
    rated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    engine_revision: str | None = None


# -- second, deliberately differently-shaped wire contract ("vendor gateway")
# --------------------------------------------------------------------------
# Proves the connector client's translation layer is a genuine adapter, not
# just RateGuard's own contract with a different name: same underlying
# deterministic engine (`rating_engine.engines.quote_service.execute_quote`),
# wrapped in a nested, differently-field-named request/response envelope
# modeled on how a policy-admin-system-style vendor quote API is commonly
# shaped (a single top-level wrapper object, camelCase field names, a nested
# "as of" date and rating-factor map). See `rating_engine.vendor_gateway`.


class VendorPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    correlationId: str
    productCode: str
    engineVersion: str
    asOfDate: date
    transactionType: TransactionType
    ratingFactors: dict[str, Any]


class VendorGatewayQuoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policyRequest: VendorPolicyRequest


class VendorPolicyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    correlationId: str
    engineVersion: str
    premiumComponents: dict[str, str]
    quotedAt: datetime = Field(default_factory=lambda: datetime.now(UTC))


class VendorGatewayQuoteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policyResponse: VendorPolicyResponse
