"""RateGuard Demo Insurer Rating Engine: a black-box REST reference engine.

An independently deployable service that prices the Arizona HO3 demo plan
behind a versioned REST contract. It imports nothing from RateGuard. In a
deployment it is private (Cloud Run IAM; no anonymous invocation) - the
platform authenticates callers before a request reaches this code, and this
service never reads a caller-supplied identity, tenant or authorization field.
This is a vendor-neutral REST reference, not a Guidewire or Duck Creek adapter.
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from pydantic import ValidationError

from rating_engine.engines import fault_injection
from rating_engine.engines.quote_service import (
    QuoteExecutionError,
    execute_quote,
    execute_quote_batch,
)
from rating_engine.engines.versions import UnknownEngineVersionError, known_engine_versions
from rating_engine.models import (
    BATCH_SCHEMA_VERSION,
    BatchQuoteRequest,
    BatchQuoteResponse,
    QuoteRequest,
    QuoteResponse,
    VendorGatewayQuoteRequest,
    VendorGatewayQuoteResponse,
)
from rating_engine.provenance import SERVICE_NAME, capabilities_document, provenance
from rating_engine.startup_selftest import run_startup_selftest
from rating_engine.vendor_gateway import handle_vendor_quote

logger = logging.getLogger(__name__)

_selftest_results: dict[str, str] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fatal by design: an exception here prevents the service from ever
    # reporting ready, per locked doc section 8.3.
    global _selftest_results
    _selftest_results = run_startup_selftest()
    logger.info("Rating-engine startup self-test passed: %s", _selftest_results)
    if fault_injection.is_active():
        logger.warning("RATING_ENGINE_FAULT_MODE is ACTIVE: demo fault injection is enabled.")
    yield


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)


@app.get("/health/live")
def health_live() -> dict[str, str]:
    return {"status": "live"}


@app.get("/health/ready")
def health_ready() -> dict[str, object]:
    return {
        "status": "ready" if _selftest_results else "not_ready",
        "engine_versions": known_engine_versions(),
        "selftest_verified": bool(_selftest_results),
        "provenance": provenance(),
    }


@app.get("/capabilities")
def capabilities() -> dict[str, object]:
    """Advertises supported engine versions, the optional batch-quote
    capability and non-sensitive provenance (vendor-neutral contract)."""
    return capabilities_document()


@app.post("/quote/batch", response_model=BatchQuoteResponse)
def quote_batch(request: BatchQuoteRequest) -> BatchQuoteResponse:
    if request.schema_version != BATCH_SCHEMA_VERSION:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported batch schema_version.")
    try:
        return execute_quote_batch(request, engine_revision=os.environ.get("K_REVISION"))
    except UnknownEngineVersionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except QuoteExecutionError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@app.post("/quote", response_model=QuoteResponse)
def quote(request: QuoteRequest) -> QuoteResponse:
    if fault_injection.should_fail(request.effective_date, request.inputs):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Injected transient fault.")
    try:
        return execute_quote(request)
    except UnknownEngineVersionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except QuoteExecutionError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@app.post("/vendor/rate-quote", response_model=VendorGatewayQuoteResponse)
def vendor_rate_quote(request: VendorGatewayQuoteRequest) -> VendorGatewayQuoteResponse:
    """The second, deliberately differently-shaped demo connector target
    (see `rating_engine.vendor_gateway`) -- a nested, camelCase envelope
    proving RateGuard's connector client adapts to a genuinely different
    wire contract, not just its own contract under a new name."""
    policy = request.policyRequest
    if fault_injection.should_fail(policy.asOfDate, policy.ratingFactors):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Injected transient fault.")
    try:
        return handle_vendor_quote(request)
    except UnknownEngineVersionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except QuoteExecutionError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
