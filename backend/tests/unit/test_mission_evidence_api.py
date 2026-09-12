"""Tests for GET /api/v1/missions/{mission_id}/evidence — the sanitized,
read-only Gemini decision evidence endpoint added for candidate/staging
verification.
"""

from fastapi.testclient import TestClient

from app.main import app
from app.storage import (
    AssuranceRunRecord,
    AssuranceRunStatus,
    EvidenceRecord,
    EvidenceType,
    get_run_store,
)

client = TestClient(app)


def _seed_mission_with_gemini_evidence(mission_id: str, *, sensitive_extra: dict | None = None) -> None:
    store = get_run_store()
    store.save_run(
        AssuranceRunRecord(run_id=mission_id, status=AssuranceRunStatus.COMPLETED, workflow_stage="COMPLETED")
    )

    data_summary = {
        "invocation_id": "GEM-ABC12345",
        "model_id": "gemini-3.7-flash",
        "auth_mode": "VERTEX_AI",
        "response_id": "resp-xyz-789",
        "prompt_version": "assurance-supervisor-v1",
        "decision_type": "PRIORITIZE_DIFFERENCES",
        "requested_tool": "prioritize_differences",
        "started_at": "2026-08-25T10:00:00+00:00",
        "ended_at": "2026-08-25T10:00:01+00:00",
        "latency_ms": 842.5,
        "input_tokens": 512,
        "output_tokens": 128,
        "success": True,
        "schema_valid": True,
        "failure_category": None,
        # These MUST never be echoed back by the sanitized endpoint:
        "rationale": "Because policy XYZ-12345 shows a $4,201.55 premium delta for territory 4B this looks material.",
        "confidence": 0.93,
        "needs_human_review": False,
    }
    if sensitive_extra:
        data_summary.update(sensitive_extra)

    evidence = EvidenceRecord(
        evidence_id="EV-GEMINI-001",
        run_id=mission_id,
        evidence_type=EvidenceType.GEMINI_INVOCATION,
        title="Gemini Invocation: PRIORITIZE_DIFFERENCES",
        description=data_summary["rationale"],
        data_summary=data_summary,
    )
    store.add_evidence(mission_id, evidence)

    # A non-Gemini evidence record must never leak into this endpoint's output.
    store.add_evidence(
        mission_id,
        EvidenceRecord(
            evidence_id="EV-SOURCE-001",
            run_id=mission_id,
            evidence_type=EvidenceType.SOURCE,
            title="Source Extraction",
            description="unrelated",
            data_summary={"filename": "rate_spec.json"},
        ),
    )


def test_evidence_endpoint_returns_whitelisted_fields_only() -> None:
    mission_id = "MIS-EVIDENCE-TEST-1"
    _seed_mission_with_gemini_evidence(mission_id)

    res = client.get(f"/api/v1/missions/{mission_id}/evidence")
    assert res.status_code == 200
    body = res.json()

    assert body["mission_id"] == mission_id
    assert body["gemini_invocation_count"] == 1
    invocation = body["gemini_invocations"][0]

    assert invocation["model_id"] == "gemini-3.7-flash"
    assert invocation["invocation_id"] == "GEM-ABC12345"
    assert invocation["response_id"] == "resp-xyz-789"
    assert invocation["decision_type"] == "PRIORITIZE_DIFFERENCES"
    assert invocation["schema_valid"] is True
    assert invocation["success"] is True
    assert invocation["failure_category"] is None
    assert invocation["requested_tool"] == "prioritize_differences"
    assert invocation["started_at"] == "2026-08-25T10:00:00+00:00"
    assert invocation["ended_at"] == "2026-08-25T10:00:01+00:00"
    assert invocation["latency_ms"] == 842.5
    assert invocation["input_tokens"] == 512
    assert invocation["output_tokens"] == 128
    assert invocation["evidence_id"] == "EV-GEMINI-001"


def test_evidence_endpoint_never_leaks_rationale_confidence_or_review_flag() -> None:
    mission_id = "MIS-EVIDENCE-TEST-2"
    _seed_mission_with_gemini_evidence(mission_id)

    res = client.get(f"/api/v1/missions/{mission_id}/evidence")
    body = res.json()
    invocation = body["gemini_invocations"][0]

    assert "rationale" not in invocation
    assert "confidence" not in invocation
    assert "needs_human_review" not in invocation
    assert "auth_mode" not in invocation
    assert "prompt_version" not in invocation

    raw_text = res.text
    assert "premium delta" not in raw_text
    assert "XYZ-12345" not in raw_text
    assert "0.93" not in raw_text


def test_evidence_endpoint_never_leaks_arbitrary_extra_data_summary_fields() -> None:
    """Even if a future evidence record's data_summary ever accidentally
    included something sensitive under an unexpected key, the whitelist
    approach must exclude it rather than passing dicts through unfiltered."""
    mission_id = "MIS-EVIDENCE-TEST-3"
    _seed_mission_with_gemini_evidence(
        mission_id,
        sensitive_extra={"raw_prompt": "SECRET SYSTEM PROMPT TEXT", "api_key_used": "AIzaFAKEKEYVALUE1234567890"},
    )

    res = client.get(f"/api/v1/missions/{mission_id}/evidence")
    raw_text = res.text
    assert "SECRET SYSTEM PROMPT" not in raw_text
    assert "AIzaFAKEKEYVALUE" not in raw_text
    assert "raw_prompt" not in raw_text
    assert "api_key_used" not in raw_text


def test_evidence_endpoint_excludes_non_gemini_evidence_records() -> None:
    mission_id = "MIS-EVIDENCE-TEST-4"
    _seed_mission_with_gemini_evidence(mission_id)

    res = client.get(f"/api/v1/missions/{mission_id}/evidence")
    body = res.json()
    assert body["gemini_invocation_count"] == 1
    assert all(inv.get("evidence_id") != "EV-SOURCE-001" for inv in body["gemini_invocations"])


def test_evidence_endpoint_404_for_unknown_mission() -> None:
    res = client.get("/api/v1/missions/MIS-DOES-NOT-EXIST-ZZZ/evidence")
    assert res.status_code == 404


def test_evidence_endpoint_empty_list_for_mission_with_no_gemini_decisions() -> None:
    mission_id = "MIS-EVIDENCE-NO-GEMINI"
    store = get_run_store()
    store.save_run(
        AssuranceRunRecord(run_id=mission_id, status=AssuranceRunStatus.COMPLETED, workflow_stage="COMPLETED")
    )

    res = client.get(f"/api/v1/missions/{mission_id}/evidence")
    assert res.status_code == 200
    body = res.json()
    assert body["gemini_invocation_count"] == 0
    assert body["gemini_invocations"] == []
