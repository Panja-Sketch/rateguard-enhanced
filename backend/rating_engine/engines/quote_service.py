"""Executes a `QuoteRequest` against one of this service's two versioned
demo engines, via the existing v0.1 oracle evaluator through the v0.2->v0.1
compatibility boundary (`app.ipir.v0_2.compat`). No network I/O, no
connector/SSRF logic — this module is the target side only (see
`backend/rating_engine/__init__.py`).
"""

from app.engines.oracle.evaluator import evaluate_package
from app.engines.oracle.models import RiskInput
from app.ipir.v0_2.compat import lower_to_v0_1
from rating_engine.engines.registry import load_engine_package
from rating_engine.models import QuoteRequest, QuoteResponse, TraceStepOut


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
