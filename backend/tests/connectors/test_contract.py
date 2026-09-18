"""Contract-model tests: extra="forbid" enforcement and trace node/operation
allowlisting (locked doc section 6.3's "extra=forbid recursively" general
convention, applied here to the connector's own contract)."""

from datetime import date

import pytest
from pydantic import ValidationError

from app.connectors.contract import (
    ConnectorQuoteRequest,
    ConnectorQuoteResponse,
    ConnectorTraceStep,
)
from app.ipir.enums import TransactionType


def test_request_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        ConnectorQuoteRequest(
            engine_version="canonical-v1",
            product="az_ho3",
            effective_date=date(2026, 10, 15),
            transaction_type=TransactionType.RENEWAL,
            inputs={"roof_age": 25},
            unexpected_field="boom",
        )


def test_response_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        ConnectorQuoteResponse(
            request_id="r1",
            engine_version="canonical-v1",
            outputs={"final_premium": "700.00"},
            rated_at="2026-10-15T00:00:00Z",
            unexpected_field="boom",
        )


def test_request_id_auto_generated_when_not_supplied():
    request = ConnectorQuoteRequest(
        engine_version="canonical-v1",
        product="az_ho3",
        effective_date=date(2026, 10, 15),
        transaction_type=TransactionType.RENEWAL,
        inputs={"roof_age": 25},
    )
    assert request.request_id


def test_trace_step_rejects_unsupported_node_type():
    with pytest.raises(ValidationError, match="unsupported trace node_type"):
        ConnectorTraceStep(node_id="n1", node_type="MALICIOUS", operation="FINAL_OUTPUT", result="1")


def test_trace_step_rejects_unsupported_operation():
    with pytest.raises(ValidationError, match="unsupported trace operation"):
        ConnectorTraceStep(node_id="n1", node_type="OUTPUT", operation="RUN_SHELL", result="1")


def test_trace_step_accepts_real_oracle_node_types_and_operations():
    step = ConnectorTraceStep(node_id="n1", node_type="CALCULATION", operation="EVALUATE_CALCULATION", result="700.00")
    assert step.result == "700.00"
