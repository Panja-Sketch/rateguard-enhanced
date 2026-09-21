"""Executes a `QuoteRequest` against one of this service's two versioned
demo engines, via the existing v0.1 oracle evaluator through the v0.2->v0.1
compatibility boundary (`app.ipir.v0_2.compat`). No network I/O, no
connector/SSRF logic — this module is the target side only (see
`backend/rating_engine/__init__.py`).
"""

from app.engines.oracle.evaluator import evaluate_package
from app.engines.oracle.models import RiskInput
from app.ipir.v0_2.compat import lower_to_v0_1
from rating_engine.engines import fault_injection
from rating_engine.engines.registry import load_engine_package
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
    returns a partial or approximate premium."""


def execute_quote(request: QuoteRequest) -> QuoteResponse:
    package = load_engine_package(request.engine_version)

    if request.product_id != package.product.product_id:
        raise QuoteExecutionError(
            f"engine_version '{request.engine_version}' serves product "
            f"'{package.product.product_id}', not '{request.product_id}'."
        )

    lowered = lower_to_v0_1(package)

    try:
        oracle_result = evaluate_package(
            package=lowered,
            risk=RiskInput(values=request.inputs),
            effective_date=request.effective_date,
            transaction_type=request.transaction_type,
        )
    except Exception as exc:  # noqa: BLE001 - re-raised as a typed, wire-safe error
        raise QuoteExecutionError(f"{type(exc).__name__}: {exc}") from exc

    trace: list[TraceStepOut] = []
    if request.trace_requested:
        trace = [
            TraceStepOut(
                node_id=step.node_id,
                node_type=step.node_type,
                operation=step.operation,
                result=str(step.result),
            )
            for step in oracle_result.trace.steps
        ]

    return QuoteResponse(
        request_id=request.request_id,
        engine_version=request.engine_version,
        outputs={"final_premium": str(oracle_result.final_premium)},
        trace=trace,
    )


def execute_quote_batch(request: BatchQuoteRequest, engine_revision: str | None = None) -> BatchQuoteResponse:
    """Rates each item independently against one engine version. The package is
    loaded and lowered once; an invalid item yields a per-item ERROR and never
    fails its neighbours."""
    package = load_engine_package(request.engine_version)  # unknown version -> raises for the whole batch
    if request.product_id != package.product.product_id:
        raise QuoteExecutionError(
            f"engine_version '{request.engine_version}' serves product "
            f"'{package.product.product_id}', not '{request.product_id}'."
        )
    lowered = lower_to_v0_1(package)

    results: list[BatchQuoteItemResult] = []
    for item in request.items:
        if fault_injection.should_fail(item.effective_date, item.inputs):
            results.append(BatchQuoteItemResult(
                item_id=item.item_id, status="ERROR",
                error=BatchItemError(code="TEMPORARILY_UNAVAILABLE", message="Injected transient fault."),
            ))
            continue
        try:
            oracle_result = evaluate_package(
                package=lowered,
                risk=RiskInput(values=item.inputs),
                effective_date=item.effective_date,
                transaction_type=item.transaction_type,
            )
        except Exception as exc:  # noqa: BLE001 - per-item, wire-safe error
            results.append(BatchQuoteItemResult(
                item_id=item.item_id, status="ERROR",
                error=BatchItemError(code="RATING_FAILED", message=type(exc).__name__),
            ))
            continue
        results.append(BatchQuoteItemResult(
            item_id=item.item_id, status="OK", outputs={"final_premium": str(oracle_result.final_premium)},
        ))
    return BatchQuoteResponse(
        batch_id=request.batch_id, engine_version=request.engine_version,
        results=results, engine_revision=engine_revision,
    )
