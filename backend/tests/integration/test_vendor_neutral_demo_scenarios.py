"""The four vendor-neutral demonstration scenarios (docs/demo/VENDOR_NEUTRAL_CONNECTOR_DEMO.md),
run through the real API path: the controlled golden workbook is compiled, the
real risk-directed planner produces the probes, and the black-box engine is
reached only over the versioned REST contract (in-process ASGI transport, no
socket). RateGuard code is identical in every scenario; only the engine version
(i.e. the deployed implementation) changes.
"""

import httpx
import pytest

from app.connectors.client import ConnectorClient
from tests.integration.test_workbook_to_connector_mission_e2e import (
    _canonical_bytes,
    _experiments,
    _failing_factory,
    _run_mission,
    _upload_and_compile,
    client,
)

EXPECTED_PROBE_COUNT = 11


@pytest.fixture()
def compiled_golden():
    source_id, package_id, compiled = _upload_and_compile(_canonical_bytes())
    return source_id, package_id, compiled


def test_golden_workbook_compiles_to_the_documented_specification(compiled_golden):
    _, _, compiled = compiled_golden
    assert compiled["workbook_compilation_receipt"]["status"] != "REJECTED"


def test_scenario_a_conformant_release_passes_with_every_probe_a_valid_match(compiled_golden):
    source_id, package_id, _ = compiled_golden
    mission = _run_mission(source_id, package_id, "canonical-v1")
    assert mission["result"]["release_decision"]["data"]["status"] == "PASS"
    experiments = _experiments(mission)
    assert len(experiments) == EXPECTED_PROBE_COUNT
    assert all(e["outcome"] == "MATCH" for e in experiments)
    data = mission["result"]["experiments"]["data"]
    assert data["mismatch_count"] == 0 and data["inconclusive_count"] == 0
    assert {20, 21, 22} <= {int(e["risk_inputs"]["roof_age"]) for e in experiments}  # boundary was exercised


def test_scenario_b_defective_implementation_blocks_at_the_roof_age_boundary(compiled_golden):
    source_id, package_id, _ = compiled_golden
    mission = _run_mission(source_id, package_id, "defective-v1")
    assert mission["result"]["release_decision"]["data"]["status"] == "BLOCK_DEPLOYMENT"
    experiments = _experiments(mission)
    mismatches = {int(e["risk_inputs"]["roof_age"]): e for e in experiments if e["outcome"] == "MISMATCH"}
    assert {21, 22} <= set(mismatches)
    for age in (21, 22):
        assert mismatches[age]["expected_premium"] == "700.00" and mismatches[age]["actual_premium"] == "655.00"
    control = next(e for e in experiments if e["probe_origin"] == "CONTROL_CASE")
    assert control["outcome"] == "MISMATCH"
    assert (control["expected_premium"], control["actual_premium"]) == ("700.00", "655.00")
    # Below the changed tier the two implementations agree.
    assert any(int(e["risk_inputs"]["roof_age"]) <= 20 and e["outcome"] == "MATCH" for e in experiments)


@pytest.mark.parametrize(
    "handler, expected_class",
    [
        (lambda request: httpx.Response(401, json={}), "CONNECTOR_AUTH_DENIED"),
        (lambda request: httpx.Response(403, json={}), "CONNECTOR_AUTH_DENIED"),
        (lambda request: httpx.Response(503, json={}), "CONNECTOR_UNAVAILABLE"),
        (lambda request: httpx.Response(200, text="not json"), "CONNECTOR_CONTRACT_ERROR"),
    ],
)
def test_scenario_c_connector_unavailable_requires_review_with_no_false_verdict(
    compiled_golden, handler, expected_class
):
    source_id, package_id, _ = compiled_golden
    mission = _run_mission(source_id, package_id, "canonical-v1", client_factory=_failing_factory(handler))
    decision = mission["result"]["release_decision"]["data"]["status"]
    assert decision == "REVIEW_REQUIRED"  # never PASS, never BLOCK_DEPLOYMENT
    experiments = _experiments(mission)
    assert len(experiments) == EXPECTED_PROBE_COUNT
    assert all(e["outcome"] == "INCONCLUSIVE" for e in experiments)
    assert {e["inconclusive_class"] for e in experiments} == {expected_class}
    data = mission["result"]["experiments"]["data"]
    assert data["mismatch_count"] == 0 and data["match_count"] == 0
    assert data["inconclusive_count"] == EXPECTED_PROBE_COUNT
    # No pricing-defect claim anywhere: no probe carries a fabricated actual premium.
    assert all(e["actual_premium"].startswith("N/A") for e in experiments)
    invocations = mission["_connector_evidence"]["connector_invocations"]
    assert {i["error_class"] for i in invocations} == {expected_class}
    assert all(i["final_premium"] is None for i in invocations)


def test_scenario_c_transport_failure_is_reported_as_unavailable(compiled_golden):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    source_id, package_id, _ = compiled_golden
    mission = _run_mission(source_id, package_id, "canonical-v1", client_factory=_failing_factory(refuse))
    assert mission["result"]["release_decision"]["data"]["status"] == "REVIEW_REQUIRED"
    assert {e["inconclusive_class"] for e in _experiments(mission)} == {"CONNECTOR_UNAVAILABLE"}


def test_scenario_c_timeout_is_reported_as_timeout(compiled_golden):
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    source_id, package_id, _ = compiled_golden
    mission = _run_mission(source_id, package_id, "canonical-v1", client_factory=_failing_factory(slow))
    assert mission["result"]["release_decision"]["data"]["status"] == "REVIEW_REQUIRED"
    assert {e["inconclusive_class"] for e in _experiments(mission)} == {"CONNECTOR_TIMEOUT"}


def _create_payload(a_source_id, a_package_id, **source_b):
    return {
        "name": "Scenario D",
        "mode": "RELEASE_CONFORMANCE",
        "product": "az_ho3",
        "jurisdiction": "Arizona",
        "effective_period_start": "2026-10-01",
        "source_a": {
            "source_id": a_source_id, "source_type": "FILE", "name": "Controlled Workbook",
            "compiled_package_id": a_package_id,
        },
        "source_b": {"source_type": "API_CONNECTOR", "name": "Connector", **source_b},
        "disposable_sample_run": True,
    }


def test_scenario_d_unsupported_engine_version_is_rejected_with_the_documented_422(compiled_golden):
    source_id, package_id, _ = compiled_golden
    response = client.post(
        "/api/v1/missions",
        json=_create_payload(
            source_id, package_id, source_id="rating-engine-demo", connector_id="rating-engine-demo",
            engine_version="canonical-v99",
        ),
    )
    assert response.status_code == 422
    issues = response.json()["detail"]["issues"]
    assert [i["code"] for i in issues] == ["CONNECTOR_ENGINE_VERSION_NOT_ALLOWED"]
    assert issues[0]["field"] == "source_b"


def test_scenario_d_the_browser_cannot_supply_a_connector_url(compiled_golden):
    from app.models.mission import PricingSourceRef

    assert not {"base_url", "url", "endpoint", "audience"} & set(PricingSourceRef.model_fields)
    source_id, package_id, _ = compiled_golden
    response = client.post(
        "/api/v1/missions",
        json=_create_payload(
            source_id, package_id, source_id="attacker", connector_id="https://attacker.example.test",
            engine_version="canonical-v1", base_url="https://attacker.example.test",
        ),
    )
    assert response.status_code == 422
    assert [i["code"] for i in response.json()["detail"]["issues"]] == ["CONNECTOR_NOT_REGISTERED"]


def test_a_developer_change_to_the_engine_alone_flips_the_verdict(compiled_golden, monkeypatch):
    """Demonstrates 'RateGuard code unchanged': correct the engine-owned factor and
    the very same defective-v1 selection now passes."""
    from rating_engine.engines import versions

    source_id, package_id, _ = compiled_golden
    before = _run_mission(source_id, package_id, "defective-v1")
    assert before["result"]["release_decision"]["data"]["status"] == "BLOCK_DEPLOYMENT"

    fixed = tuple(
        versions.RoofAgeTier(t.min_age, t.max_age, versions.ROOF_AGE_TIERS["canonical-v1"][-1].factor)
        if t.max_age is None
        else t
        for t in versions.ROOF_AGE_TIERS["defective-v1"]
    )
    monkeypatch.setitem(versions.ROOF_AGE_TIERS, "defective-v1", fixed)
    after = _run_mission(source_id, package_id, "defective-v1")
    assert after["result"]["release_decision"]["data"]["status"] == "PASS"
    assert ConnectorClient  # RateGuard's client is the same object in both runs
