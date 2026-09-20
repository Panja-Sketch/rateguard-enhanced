"""SEEDED-PLAN unit tests of the supervisor's connector decision logic.

These deliberately replace the test planner with a fixed single-scenario plan
so they isolate *decision semantics* (proven mismatch vs equivalence vs
inconclusive evidence). They are NOT evidence that the real planner produces
the right probes -- that is proven, without any planner monkeypatch, by
`tests/integration/test_workbook_to_connector_mission_e2e.py` and
`tests/testing/test_package_probes.py`.

Original description: integration-level tests proving the locked golden case ($700.00 canonical
vs $655.00 defective, exact first-divergent-node) through the real
`AssuranceSupervisor.run_mission` connector path -- Source A is the golden
IPIR v0.2 package (lowered to v0.1), Source B is a live REST connector call
against the real `backend/rating_engine` demo service, wired through
`httpx.ASGITransport` (no real network) exactly as `tests/connectors/`
already does.

Also covers the required negative connector scenarios (requirement 4 of the
Session 4 task): connector timeout/hard failure and a partial-response
failure category must never produce PASS.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from app.agents.supervisor import AssuranceSupervisor
from app.connectors.client import ConnectorClient
from app.connectors.errors import ConnectorException, ConnectorFailureCategory
from app.engines.testing.models import PricingTestPlan, PricingTestScenario, ScenarioClassification
from app.ipir.v0_2.compat import lower_to_v0_1
from app.ipir.v0_2.package import IPIRPackageV2
from app.models.mission import (
    AssuranceMission,
    ComparisonMode,
    ConnectorSelection,
    MissionObjective,
    PricingSourceRef,
)
from app.models.result_v2 import AnalysisStatus
from app.storage.memory_store import InMemoryRunStore
from rating_engine.main import app as real_rating_engine_app

GOLDEN_FIXTURE_DIR = (
    Path(__file__).resolve().parents[3] / "data" / "implementations" / "v0_2" / "canonical"
)
GOLDEN_FIXTURE_PATH = GOLDEN_FIXTURE_DIR / "AZ_HO3_GOLDEN_ipir.json"


def _load_left_pkg():
    """The exact golden Source A package (roof_age=25 -> $700.00 canonical),
    lowered from v0.2 to the v0.1 shape `run_mission` expects for Source A."""
    pkg_v2 = IPIRPackageV2.model_validate_json(GOLDEN_FIXTURE_PATH.read_text(encoding="utf-8"))
    return lower_to_v0_1(pkg_v2)


def _golden_scenario() -> PricingTestScenario:
    """The exact locked golden case's risk inputs (roof_age=25), matching
    `rating_engine/startup_selftest.py::GOLDEN_CASE_INPUTS` exactly so the
    connector reproduces its own proven $700.00/$655.00 self-test values."""
    return PricingTestScenario(
        id="RG_GOLDEN_001",
        name="Locked Golden Case (roof_age=25)",
        # `effective_date` inside risk_values drives PremiumOracleCalculator
        # (app.engines.oracle.calculator) directly -- it does not read the
        # scenario's own `effective_date` field for that purpose.
        risk_values={"roof_age": 25, "dwelling_limit": "300000.00", "effective_date": "2026-10-01"},
        effective_date="2026-10-01",
        classification=ScenarioClassification.CONTROL,
        purpose="Reproduce the locked golden case through a live connector.",
    )


def _single_scenario_plan(pkg_id: str) -> PricingTestPlan:
    scenario = _golden_scenario()
    return PricingTestPlan(
        package_id=pkg_id,
        candidate_count=1,
        selected_count=1,
        selected_scenarios=[scenario],
        candidate_scenarios=[scenario],
        coverage_metrics={"candidate_reduction_pct": 0.0},
    )


def _mission(connector_id: str, engine_version: str) -> AssuranceMission:
    return AssuranceMission(
        mission_id=f"MIS-CONNECTOR-{engine_version.upper()}",
        name="Workbook vs Connector Release Conformance",
        mode=ComparisonMode.RELEASE_CONFORMANCE,
        objective=MissionObjective(product="az_ho3", jurisdiction="Arizona", effective_period_start="2026-10-01"),
        source_a=PricingSourceRef(source_id="WORKBOOK-GOLDEN", source_type="FILE", name="Controlled Workbook v1 (Golden)"),
        source_b=PricingSourceRef(
            source_id=connector_id, source_type="API_CONNECTOR", name="Rating Engine Connector",
            connector_id=connector_id, engine_version=engine_version,
        ),
    )


def _supervisor_with_real_demo_connector() -> AssuranceSupervisor:
    """A real `ConnectorClient` wired to the real `rating_engine` app over
    `httpx.ASGITransport` -- real HTTP/Pydantic semantics, no socket."""
    store = InMemoryRunStore()
    transport = httpx.ASGITransport(app=real_rating_engine_app)
    supervisor = AssuranceSupervisor(
        store, connector_client_factory=lambda: ConnectorClient(transport=transport)
    )
    supervisor.test_generator.generate_plan = lambda pkg, diff, impact, **_kw: _single_scenario_plan(pkg.id)
    return supervisor


def test_golden_case_canonical_connector_passes():
    left_pkg = _load_left_pkg()
    supervisor = _supervisor_with_real_demo_connector()
    mission = _mission("rating-engine-demo", "canonical-v1")

    result = supervisor.run_mission(
        mission, left_pkg,
        target_connector=ConnectorSelection(connector_id="rating-engine-demo", engine_version="canonical-v1"),
    )

    assert result.experiments.data.mismatch_count == 0
    exp = result.experiments.data.experiments[0]
    assert exp.expected_premium == "700.00"
    assert exp.actual_premium == "700.00"
    assert result.release_decision.data.status == "PASS"


def test_golden_case_defective_connector_blocks_with_first_divergent_node():
    left_pkg = _load_left_pkg()
    supervisor = _supervisor_with_real_demo_connector()
    mission = _mission("rating-engine-demo", "defective-v1")

    result = supervisor.run_mission(
        mission, left_pkg,
        target_connector=ConnectorSelection(connector_id="rating-engine-demo", engine_version="defective-v1"),
    )

    exp = result.experiments.data.experiments[0]
    assert exp.expected_premium == "700.00"
    assert exp.actual_premium == "655.00"
    assert result.release_decision.data.status == "BLOCK_DEPLOYMENT"
    assert result.reconciliation.data.mismatch_count == 1
    assert result.reconciliation.data.first_divergent_node is not None
    assert result.reconciliation.data.root_cause.expected_value == "700.00"
    assert result.reconciliation.data.root_cause.actual_value == "655.00"

    # Every stage is accounted for, never silently missing.
    assert result.stage_outcomes, "stage_outcomes must never be empty for a completed mission"
    recorded_stages = {o.stage for o in result.stage_outcomes}
    from app.models.stages import MISSION_STAGE_ORDER

    assert recorded_stages == set(MISSION_STAGE_ORDER)


def test_connector_hard_failure_requires_review_and_is_not_reported_as_a_pricing_defect():
    """Every probe fails outright (connector unreachable/timed out). That is
    inconclusive evidence: REVIEW_REQUIRED (locked doc 7.4), never PASS and
    never BLOCK_DEPLOYMENT, which would misrepresent an operational failure as
    a confirmed price disagreement."""
    left_pkg = _load_left_pkg()
    store = InMemoryRunStore()

    class _AlwaysFailsClient:
        async def send_quote(self, *args, **kwargs):
            raise ConnectorException(
                code="CONNECTOR_TIMEOUT",
                message="Simulated connector timeout.",
                category=ConnectorFailureCategory.RETRYABLE,
            )

    supervisor = AssuranceSupervisor(store, connector_client_factory=lambda: _AlwaysFailsClient())
    supervisor.test_generator.generate_plan = lambda pkg, diff, impact, **_kw: _single_scenario_plan(pkg.id)
    mission = _mission("rating-engine-demo", "canonical-v1")

    result = supervisor.run_mission(
        mission, left_pkg,
        target_connector=ConnectorSelection(connector_id="rating-engine-demo", engine_version="canonical-v1"),
    )

    decision = result.release_decision.data
    assert decision.status == "REVIEW_REQUIRED"
    assert any("inconclusive" in r.lower() for r in decision.blocking_reasons)
    assert all("mismatch" not in r.lower() for r in decision.blocking_reasons)
    assert result.experiments.data.mismatch_count == 0
    assert result.experiments.data.inconclusive_count == 1
    assert result.experiments.data.experiments[0].outcome == "INCONCLUSIVE"
    assert result.reconciliation.status != AnalysisStatus.SUCCEEDED  # no fabricated root cause


def test_connector_partial_response_forces_review_required_not_pass():
    """A REVIEW_REQUIRED-category connector failure (e.g. incomplete output
    batch) must never be silently absorbed into PASS, and must not be
    misrepresented as a confirmed BLOCK_DEPLOYMENT price mismatch either."""
    left_pkg = _load_left_pkg()
    store = InMemoryRunStore()

    class _PartialResponseClient:
        async def send_quote(self, *args, **kwargs):
            raise ConnectorException(
                code="CONNECTOR_INCOMPLETE_OUTPUT_BATCH",
                message="Simulated partial/incomplete connector response.",
                category=ConnectorFailureCategory.REVIEW_REQUIRED,
            )

    supervisor = AssuranceSupervisor(store, connector_client_factory=lambda: _PartialResponseClient())
    supervisor.test_generator.generate_plan = lambda pkg, diff, impact, **_kw: _single_scenario_plan(pkg.id)
    mission = _mission("rating-engine-demo", "canonical-v1")

    result = supervisor.run_mission(
        mission, left_pkg,
        target_connector=ConnectorSelection(connector_id="rating-engine-demo", engine_version="canonical-v1"),
    )

    assert result.release_decision.data.status != "PASS"


def test_proven_mismatch_still_blocks_even_when_other_probes_are_inconclusive():
    """A real, proven premium mismatch is never downgraded by unrelated
    connector failures on other probes."""
    left_pkg = _load_left_pkg()
    store = InMemoryRunStore()
    transport = httpx.ASGITransport(app=real_rating_engine_app)
    real_client = ConnectorClient(transport=transport)

    class _FailsOnRoofAge30:
        async def send_quote(self, connector_id, engine_version, request, **kwargs):
            if request.inputs.get("roof_age") == 30:
                raise ConnectorException(
                    code="CONNECTOR_TIMEOUT", message="Simulated timeout.",
                    category=ConnectorFailureCategory.RETRYABLE,
                )
            return await real_client.send_quote(connector_id, engine_version, request, **kwargs)

    second = _golden_scenario().model_copy(
        update={"id": "RG_GOLDEN_002", "name": "Second probe (roof_age=30)",
                "risk_values": {"roof_age": 30, "dwelling_limit": "300000.00"}}
    )
    first = _golden_scenario().model_copy(update={"risk_values": {"roof_age": 25, "dwelling_limit": "300000.00"}})
    plan = PricingTestPlan(
        package_id=left_pkg.id, candidate_count=2, selected_count=2,
        selected_scenarios=[first, second], candidate_scenarios=[first, second],
        coverage_metrics={"candidate_reduction_pct": 0.0},
    )
    supervisor = AssuranceSupervisor(store, connector_client_factory=lambda: _FailsOnRoofAge30())
    supervisor.test_generator.generate_plan = lambda pkg, diff, impact, **_kw: plan

    result = supervisor.run_mission(
        _mission("rating-engine-demo", "defective-v1"), left_pkg,
        target_connector=ConnectorSelection(connector_id="rating-engine-demo", engine_version="defective-v1"),
    )

    assert result.release_decision.data.status == "BLOCK_DEPLOYMENT"
    assert result.experiments.data.mismatch_count == 1
    assert result.experiments.data.inconclusive_count == 1
    assert result.reconciliation.data.root_cause.divergence_type == "CONNECTOR_PREMIUM_MISMATCH"
    assert result.reconciliation.data.root_cause.actual_value == "655.00"


def test_probe_with_out_of_period_calculation_date_is_inconclusive_not_a_mismatch_or_pass():
    left_pkg = _load_left_pkg()
    store = InMemoryRunStore()
    bad = _golden_scenario().model_copy(
        update={"effective_date": "2026-09-15", "risk_values": {"roof_age": 25, "dwelling_limit": "300000.00"}}
    )
    plan = PricingTestPlan(
        package_id=left_pkg.id, candidate_count=1, selected_count=1,
        selected_scenarios=[bad], candidate_scenarios=[bad], coverage_metrics={"candidate_reduction_pct": 0.0},
    )
    supervisor = AssuranceSupervisor(
        store, connector_client_factory=lambda: ConnectorClient(transport=httpx.ASGITransport(app=real_rating_engine_app))
    )
    supervisor.test_generator.generate_plan = lambda pkg, diff, impact, **_kw: plan

    result = supervisor.run_mission(
        _mission("rating-engine-demo", "canonical-v1"), left_pkg,
        target_connector=ConnectorSelection(connector_id="rating-engine-demo", engine_version="canonical-v1"),
    )

    exp = result.experiments.data.experiments[0]
    assert exp.outcome == "INCONCLUSIVE"
    assert "CALCULATION_DATE_OUT_OF_PERIOD" in exp.inconclusive_reason
    assert result.release_decision.data.status == "REVIEW_REQUIRED"
    assert result.experiments.data.mismatch_count == 0


def test_empty_plan_requires_review_never_pass():
    left_pkg = _load_left_pkg()
    store = InMemoryRunStore()
    empty = PricingTestPlan(package_id=left_pkg.id, candidate_count=0, selected_count=0,
                            coverage_metrics={"candidate_reduction_pct": 0.0})
    supervisor = AssuranceSupervisor(
        store, connector_client_factory=lambda: ConnectorClient(transport=httpx.ASGITransport(app=real_rating_engine_app))
    )
    supervisor.test_generator.generate_plan = lambda pkg, diff, impact, **_kw: empty

    result = supervisor.run_mission(
        _mission("rating-engine-demo", "canonical-v1"), left_pkg,
        target_connector=ConnectorSelection(connector_id="rating-engine-demo", engine_version="canonical-v1"),
    )
    assert result.release_decision.data.status == "REVIEW_REQUIRED"
    assert any("No probe could be executed" in r for r in result.release_decision.data.blocking_reasons)
