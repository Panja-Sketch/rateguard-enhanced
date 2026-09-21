"""The connector's own versioned request/response contract (CP8).

Deliberately richer than `backend/rating_engine`'s raw wire format
(`rating_engine.models.QuoteRequest`/`QuoteResponse`) — the connector may
talk to a different target implementation in the future even though only
one exists today. `app.connectors.client` translates between this contract
and whatever wire shape a specific target expects.

Every model forbids unknown fields (`extra="forbid"`), matching the general
API-contract convention already used elsewhere in this codebase
(`rating_engine/models.py`, `app/ipir/v0_2/*`).

Jurisdiction judgment call (see docs/implementation/DECISIONS.md D7 for the
full rationale): `backend/rating_engine`'s current `QuoteRequest` has no
`jurisdiction` field — its one fixture is jurisdiction-fixed (AZ HO3). This
connector contract carries `jurisdiction` as an optional field so a caller
that supplies it is never silently dropped: `app.connectors.client` records
it in the connector's own request/response objects and logs it, but does
not forward it onto the current demo target's wire payload (which would be
rejected by that target's `extra="forbid"` `QuoteRequest` model anyway). A
future target that natively supports jurisdiction can be wired to forward
it without changing this contract.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.ipir.enums import TransactionType

# Allowlists for calculation-trace nodes (locked doc section 8.2's general
# "strict schemas" posture applied to trace output specifically). Mirrors
# the actual node_type/operation vocabulary the real oracle evaluator
# produces (`app/engines/oracle/evaluator.py`) plus the modifier/constraint
# enum values it reuses as `operation` — not an arbitrary hypothetical list.
TRACE_NODE_TYPE_ALLOWLIST: frozenset[str] = frozenset(
    {
        "INPUT",
        "CONSTANT",
        "TABLE",
        "RULE",
        "MODIFIER",
        "CONSTRAINT",
        "FEE",
        "CALCULATION",
        "OUTPUT",
    }
)

TRACE_OPERATION_ALLOWLIST: frozenset[str] = frozenset(
    {
        "SET_INPUT",
        "SET_CONSTANT",
        "LOOKUP_TABLE",
        "EVALUATE_RULE",
        "SKIPPED_INELIGIBLE",
        "ADD_FEE",
        "EVALUATE_CALCULATION",
        "FINAL_OUTPUT",
        "PERCENTAGE_DISCOUNT",
        "PERCENTAGE_SURCHARGE",
        "FLAT_DISCOUNT",
        "FLAT_SURCHARGE",
        "MINIMUM",
        "MAXIMUM",
    }
)


class ConnectorTraceStep(BaseModel):
    """Mirrors `rating_engine.models.TraceStepOut`'s shape, plus an explicit
    allowlist check on `node_type`/`operation` — an "unsupported trace node"
    must be a specific, typed rejection, not silently passed through."""

    model_config = ConfigDict(extra="forbid")

    node_id: str
    node_type: str
    operation: str
    result: str

    @field_validator("node_type")
    @classmethod
    def _node_type_allowed(cls, value: str) -> str:
        if value not in TRACE_NODE_TYPE_ALLOWLIST:
            raise ValueError(f"unsupported trace node_type '{value}'")
        return value

    @field_validator("operation")
    @classmethod
    def _operation_allowed(cls, value: str) -> str:
        if value not in TRACE_OPERATION_ALLOWLIST:
            raise ValueError(f"unsupported trace operation '{value}'")
        return value


class ConnectorQuoteRequest(BaseModel):
    """The connector's own request contract. `product` is a plain string
    (mirrors the target's `product_id` directly — see module docstring for
    why a structured object was not chosen: for this challenge scope there
    is exactly one target and its wire contract already uses a plain
    string id, so a structured `product` object would add a translation
    layer with no current consumer)."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    engine_version: str
    product: str
    jurisdiction: str | None = None
    effective_date: date
    transaction_type: TransactionType
    inputs: dict[str, Any]
    trace_requested: bool = False


class ConnectorQuoteResponse(BaseModel):
    """The connector's own response contract. `outputs` is decimal-string
    only — `app.connectors.client` explicitly rejects a JSON-number output
    before this model is ever constructed (see client.py for why relying on
    Pydantic's own coercion behavior alone was judged insufficient)."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    engine_version: str
    outputs: dict[str, str]
    trace: list[ConnectorTraceStep] = Field(default_factory=list)
    rated_at: datetime
    jurisdiction: str | None = None


# -- optional batch-quote capability (`quote-batch-v1`) -----------------------
#
# A connector MAY advertise `GET /capabilities` ->
# {"quote_batch": {"schema_version": "quote-batch-v1", "max_items": N}}.
# RateGuard then sends up to N independent items per request; a connector that
# does not advertise it is driven with the single-quote contract above. Items
# are correlated by `item_id` (an opaque per-job row reference), never by
# position.

BATCH_SCHEMA_VERSION = "quote-batch-v1"
BATCH_MAX_ITEMS = 250
MAX_BATCH_REQUEST_BYTES = 512 * 1024


class ConnectorBatchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    effective_date: date
    transaction_type: TransactionType
    inputs: dict[str, Any]


class ConnectorBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = BATCH_SCHEMA_VERSION
    batch_id: str
    engine_version: str
    product_id: str
    items: list[ConnectorBatchItem] = Field(min_length=1, max_length=BATCH_MAX_ITEMS)

    @field_validator("schema_version")
    @classmethod
    def _version_supported(cls, value: str) -> str:
        if value != BATCH_SCHEMA_VERSION:
            raise ValueError(f"unsupported batch schema_version '{value}'")
        return value

    @field_validator("items")
    @classmethod
    def _unique_item_ids(cls, items: list[ConnectorBatchItem]) -> list[ConnectorBatchItem]:
        if len({i.item_id for i in items}) != len(items):
            raise ValueError("item_id values must be unique within a batch")
        return items


class ConnectorBatchItemError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str = ""


class ConnectorBatchItemResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    status: str  # "OK" | "ERROR"
    outputs: dict[str, str] = Field(default_factory=dict)
    error: ConnectorBatchItemError | None = None

    @field_validator("status")
    @classmethod
    def _status_known(cls, value: str) -> str:
        if value not in ("OK", "ERROR"):
            raise ValueError(f"unsupported item status '{value}'")
        return value


class ConnectorBatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = BATCH_SCHEMA_VERSION
    batch_id: str
    engine_version: str
    results: list[ConnectorBatchItemResult]
    rated_at: datetime
    # Rating-engine build/revision identifier when the target reports one.
    engine_revision: str | None = None
