"""The isolated demo rating-engine service entry point (locked doc sections
8.3, 12.2). Not unauthenticated in a real deployment — per section 12.2 this
service is "private/authenticated if deployed separately; no public
anonymous invocation" — but authentication/networking wiring is out of scope
for this session (see docs/implementation/DECISIONS.md, D3) and is added
alongside the connector client in a later session.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from pydantic import ValidationError

from rating_engine.engines.quote_service import QuoteExecutionError, execute_quote
from rating_engine.engines.registry import UnknownEngineVersionError, known_engine_versions
from rating_engine.models import QuoteRequest, QuoteResponse
from rating_engine.startup_selftest import run_startup_selftest

logger = logging.getLogger(__name__)

_selftest_results: dict[str, str] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fatal by design: an exception here prevents the service from ever
    # reporting ready, per locked doc section 8.3.
    global _selftest_results
    _selftest_results = run_startup_selftest()
    logger.info("Rating-engine startup self-test passed: %s", _selftest_results)
    yield


app = FastAPI(title="RateGuard Demo Rating Engine", lifespan=lifespan)


@app.get("/health/live")
def health_live() -> dict[str, str]:
    return {"status": "live"}


@app.get("/health/ready")
def health_ready() -> dict[str, object]:
    return {
        "status": "ready" if _selftest_results else "not_ready",
        "engine_versions": known_engine_versions(),
        "selftest_verified": bool(_selftest_results),
    }


@app.post("/quote", response_model=QuoteResponse)
def quote(request: QuoteRequest) -> QuoteResponse:
    try:
        return execute_quote(request)
    except UnknownEngineVersionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except QuoteExecutionError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
