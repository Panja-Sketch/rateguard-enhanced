"""Executes quote requests against the engine's own rating tables
(`rating_engine.engines.versions`). Self-contained: no RateGuard import, no
network I/O, no connector/SSRF logic - this module is the target side only.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from rating_engine.engines import fault_injection
from rating_engine.engines.versions import (
    BASE_RATE,
    EFFECTIVE_FROM,
    PRODUCT_ID,
    REQUIRED_INPUTS,
    SUPPORTED_TRANSACTION_TYPES,
    tiers_for,
)
from rating_engine.models import (
    BatchItemError,
    BatchQuoteItemResult,
    BatchQuoteRequest,
    BatchQuoteResponse,
    QuoteRequest,
    QuoteResponse,
    TraceStepOut,
)


class QuoteExecutionError(Exception):
    """Raised when a request cannot be rated (missing/invalid inputs,
    incompatible product/effective-date/transaction-type). Never silently
    returns a partial or approximate premium. Messages never echo input
    values."""


def _parse_roof_age(raw: Any) -> int:
    if isinstance(raw, bool):
        raise QuoteExecutionError("Input 'roof_age' must be a whole number.")
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise QuoteExecutionError("Input 'roof_age' must be a whole number.") from exc
    if not value.is_finite() or value != value.to_integral_value():
        raise QuoteExecutionError("Input 'roof_age' must be a whole number.")
    if value < 0:
        raise QuoteExecutionError("Input 'roof_age' must not be negative.")
    return int(value)


def _validate_dwelling_limit(raw: Any) -> None:
    if isinstance(raw, bool):
        raise QuoteExecutionError("Input 'dwelling_limit' must be a monetary amount.")
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise QuoteExecutionError("Input 'dwelling_limit' must be a monetary amount.") from exc
    if not value.is_finite() or value < 0:
        raise QuoteExecutionError("Input 'dwelling_limit' must be a non-negative monetary amount.")


def _validate_context(product_id: str, engine_version: str, effective_date: date, transaction_type: str) -> None:
    tiers_for(engine_version)  # raises UnknownEngineVersionError
    if product_id != PRODUCT_ID:
        raise QuoteExecutionError(f"engine_version '{engine_version}' serves product '{PRODUCT_ID}', not '{product_id}'.")
    if effective_date < EFFECTIVE_FROM:
        raise QuoteExecutionError(f"The plan is not effective before {EFFECTIVE_FROM.isoformat()}.")
    if transaction_type not in SUPPORTED_TRANSACTION_TYPES:
        raise QuoteExecutionError(f"Transaction type '{transaction_type}' is not supported.")


def _rate(engine_version: str, inputs: dict[str, Any]) -> tuple[int, Decimal, Decimal, Decimal]:
    """Returns (roof_age, factor, raw_premium, final_premium)."""
    for name in REQUIRED_INPUTS:
        if inputs.get(name) is None:
            raise QuoteExecutionError(f"Missing required rating input: '{name}'.")
    roof_age = _parse_roof_age(inputs["roof_age"])
    _validate_dwelling_limit(inputs["dwelling_limit"])
    factor = next((t.factor for t in tiers_for(engine_version) if t.contains(roof_age)), None)
    if factor is None:  # pragma: no cover - tiers are contiguous from 0
        raise QuoteExecutionError("No rating tier matches the supplied roof_age.")
    raw = BASE_RATE * factor
    final = raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return roof_age, factor, raw, final


def execute_quote(request: QuoteRequest) -> QuoteResponse:
    _validate_context(request.product_id, request.engine_version, request.effective_date, request.transaction_type.value)
    roof_age, factor, raw, final = _rate(request.engine_version, request.inputs)

    trace: list[TraceStepOut] = []
    if request.trace_requested:
        trace = [
            TraceStepOut(node_id="roof_age", node_type="INPUT", operation="SET_INPUT", result=str(roof_age)),
            TraceStepOut(node_id="base_rate", node_type="CONSTANT", operation="SET_CONSTANT", result=str(BASE_RATE)),
            TraceStepOut(
                node_id="roof_age_factor_table", node_type="TABLE", operation="LOOKUP_TABLE", result=str(factor)
            ),
            TraceStepOut(
                node_id="raw_premium", node_type="CALCULATION", operation="EVALUATE_CALCULATION", result=str(raw)
            ),
            TraceStepOut(
                node_id="final_premium", node_type="CALCULATION", operation="EVALUATE_CALCULATION", result=str(final)
            ),
            TraceStepOut(
                node_id="final_premium_output", node_type="OUTPUT", operation="FINAL_OUTPUT", result=str(final)
            ),
        ]
    return QuoteResponse(
        request_id=request.request_id,
        engine_version=request.engine_version,
        outputs={"final_premium": str(final)},
        trace=trace,
    )


def execute_quote_batch(request: BatchQuoteRequest, engine_revision: str | None = None) -> BatchQuoteResponse:
    """Rates each item independently against one engine version. An invalid
    item yields a per-item ERROR and never fails its neighbours."""
    tiers_for(request.engine_version)  # unknown version -> raises for the whole batch
    if request.product_id != PRODUCT_ID:
        raise QuoteExecutionError(
            f"engine_version '{request.engine_version}' serves product '{PRODUCT_ID}', not '{request.product_id}'."
        )

    results: list[BatchQuoteItemResult] = []
    for item in request.items:
        if fault_injection.should_fail(item.effective_date, item.inputs):
            results.append(
                BatchQuoteItemResult(
                    item_id=item.item_id,
                    status="ERROR",
                    error=BatchItemError(code="TEMPORARILY_UNAVAILABLE", message="Injected transient fault."),
                )
            )
            continue
        try:
            _validate_context(request.product_id, request.engine_version, item.effective_date, item.transaction_type.value)
            _, _, _, final = _rate(request.engine_version, item.inputs)
        except QuoteExecutionError:
            results.append(
                BatchQuoteItemResult(item_id=item.item_id, status="ERROR", error=BatchItemError(code="RATING_FAILED"))
            )
            continue
        results.append(
            BatchQuoteItemResult(item_id=item.item_id, status="OK", outputs={"final_premium": str(final)})
        )
    return BatchQuoteResponse(
        batch_id=request.batch_id,
        engine_version=request.engine_version,
        results=results,
        engine_revision=engine_revision,
    )
