"""Startup self-test (locked doc section 8.3): "Startup self-tests must
prove both values before the service becomes ready." Runs the exact golden
case against both `canonical-v1` and `defective-v1` and raises if either
does not match — this is meant to be called from the FastAPI app's lifespan
startup so the service crashes on boot rather than serving traffic with a
silently broken demo engine.
"""

from datetime import date

from rating_engine.engines.quote_service import execute_quote
from rating_engine.models import QuoteRequest, TransactionType

GOLDEN_CASE_INPUTS = {"roof_age": 25, "dwelling_limit": "300000.00"}
GOLDEN_EFFECTIVE_DATE = date(2026, 10, 1)
GOLDEN_EXPECTED = {
    "canonical-v1": "700.00",
    "defective-v1": "655.00",
}


class StartupSelfTestFailedError(RuntimeError):
    """Raised when the demo engine cannot reproduce its own locked golden
    value. Deliberately fatal — this service must never become ready while
    it cannot prove the exact result section 8.3 requires."""


def run_startup_selftest() -> dict[str, str]:
    """Returns {engine_version: actual_premium} on success; raises
    `StartupSelfTestFailedError` on any mismatch or execution failure."""
    results: dict[str, str] = {}
    failures: list[str] = []

    for engine_version, expected in GOLDEN_EXPECTED.items():
        try:
            response = execute_quote(
                QuoteRequest(
                    request_id=f"startup-selftest-{engine_version}",
                    engine_version=engine_version,
                    product_id="az_ho3",
                    effective_date=GOLDEN_EFFECTIVE_DATE,
                    transaction_type=TransactionType.NEW_BUSINESS,
                    inputs=GOLDEN_CASE_INPUTS,
                )
            )
            actual = response.outputs.get("final_premium")
            results[engine_version] = actual or ""
            if actual != expected:
                failures.append(
                    f"{engine_version}: expected {expected}, got {actual!r}"
                )
        except Exception as exc:  # noqa: BLE001 - collected, then raised together
            failures.append(f"{engine_version}: {type(exc).__name__}: {exc}")

    if failures:
        raise StartupSelfTestFailedError(
            "Rating-engine startup self-test failed for: " + "; ".join(failures)
        )

    return results
