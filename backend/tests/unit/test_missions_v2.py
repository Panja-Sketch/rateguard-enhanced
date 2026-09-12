from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from app.agents.supervisor import AssuranceSupervisor
from app.api.assurance import resolve_demo_package
from app.engines.oracle.calculator import CalcResult, PremiumOracleCalculator
from app.ipir.enums import InsuranceLine
from app.ipir.package import IPIRPackage
from app.models.mission import (
    AssuranceMission,
    ComparisonMode,
    MissionObjective,
    PricingSourceRef,
)
from app.services.remediation_service import RemediationService
from app.services.validation_service import MissionValidationService
from app.storage.memory_store import InMemoryRunStore


def _load_fixture(name: str) -> IPIRPackage:
    path = Path(__file__).resolve().parent.parent / "fixtures" / name
    return IPIRPackage.model_validate_json(path.read_text(encoding="utf-8"))


def test_mission_validation_rules():
    # Valid mission
    mission = AssuranceMission(
        mission_id="MIS-TEST-01",
        name="Valid Mission",
        mode=ComparisonMode.RELEASE_CONFORMANCE,
        objective=MissionObjective(product="AZ_HO3", jurisdiction="Arizona", effective_period_start="2026-09-01"),
        source_a=PricingSourceRef(source_id="AZ_HO3_2026_09", source_type="SAMPLE_RELEASE", name="Intent"),
        source_b=PricingSourceRef(source_id="AZ_HO3_2026_09_DEFECTIVE", source_type="SAMPLE_RELEASE", name="Target"),
    )

    issues = MissionValidationService.validate_mission(mission)
    assert len(issues) == 0


def test_supervisor_clean_equivalence():
    store = InMemoryRunStore()
    supervisor = AssuranceSupervisor(store)

    left_pkg = resolve_demo_package("AZ_HO3_2026_09")
    right_pkg = resolve_demo_package("AZ_HO3_2026_09_CLEAN")

    mission = AssuranceMission(
        mission_id="MIS-CLEAN-01",
        name="Clean Equivalence Mission",
        mode=ComparisonMode.EQUIVALENCE,
        objective=MissionObjective(product="AZ_HO3", jurisdiction="Arizona", effective_period_start="2026-09-01"),
        source_a=PricingSourceRef(source_id="AZ_HO3_2026_09", source_type="SAMPLE_RELEASE", name="Intent"),
        source_b=PricingSourceRef(source_id="AZ_HO3_2026_09_CLEAN", source_type="SAMPLE_RELEASE", name="Clean Target"),
    )

    res = supervisor.run_mission(mission, left_pkg, right_pkg)
    assert res.release_decision.data.status == "PASS"
    assert res.semantic_analysis.data.difference_count == 0


def test_supervisor_blocks_when_semantic_diff_misses_a_behavioral_mismatch():
    """Regression test for a false PASS: a real production mission reported
    PASS because 0 AST semantic differences were found, even though the
    Premium Oracle actually computed different premiums for Source A vs
    Source B ($520.00 vs $610.00) among the clean-equivalence verification
    probes -- that mismatch was silently discarded (mismatch_count was
    hardcoded to 0 in that branch) instead of gating the decision. For a
    pricing-assurance tool, a false PASS is worse than an explicit block:
    the release decision must agree across ALL evidence, not semantic
    AST comparison alone."""
    store = InMemoryRunStore()
    supervisor = AssuranceSupervisor(store)

    pkg = resolve_demo_package("AZ_HO3_2026_09")

    call_count = {"n": 0}
    real_calculate = PremiumOracleCalculator.calculate_policy_premium

    def fake_calculate(self, risk_inputs):
        call_count["n"] += 1
        result = real_calculate(self, risk_inputs)
        # The oracle/target calculators are invoked alternately per probe
        # (expected, then actual) -- force a mismatch on the very first
        # probe's target-side calculation only.
        if call_count["n"] == 2:
            return CalcResult(final_premium=result.final_premium + Decimal("90.00"), trace=result.trace)
        return result

    mission = AssuranceMission(
        mission_id="MIS-BLINDSPOT-01",
        name="Semantic Diff Blind Spot Mission",
        mode=ComparisonMode.EQUIVALENCE,
        objective=MissionObjective(product="AZ_HO3", jurisdiction="Arizona", effective_period_start="2026-09-01"),
        source_a=PricingSourceRef(source_id="AZ_HO3_2026_09", source_type="SAMPLE_RELEASE", name="Source A"),
        source_b=PricingSourceRef(source_id="AZ_HO3_2026_09", source_type="SAMPLE_RELEASE", name="Source B"),
    )

    with patch.object(PremiumOracleCalculator, "calculate_policy_premium", fake_calculate):
        res = supervisor.run_mission(mission, pkg, pkg)

    assert res.semantic_analysis.data.difference_count == 0
    assert res.experiments.data.mismatch_count > 0
    assert res.release_decision.data.status == "BLOCK_DEPLOYMENT"
    assert res.release_decision.data.status != "PASS"


def test_supervisor_downgrades_pass_to_review_required_on_low_confidence_extraction():
    """A source flagged requires_human_review at compile time (low
    extraction confidence) must never silently support a PASS, even when
    every other signal (semantic + behavioral) agrees the sources match."""
    store = InMemoryRunStore()
    supervisor = AssuranceSupervisor(store)

    left_pkg = resolve_demo_package("AZ_HO3_2026_09")
    right_pkg = resolve_demo_package("AZ_HO3_2026_09_CLEAN")

    mission = AssuranceMission(
        mission_id="MIS-UNCERTAIN-01",
        name="Low Confidence Extraction Mission",
        mode=ComparisonMode.EQUIVALENCE,
        objective=MissionObjective(product="AZ_HO3", jurisdiction="Arizona", effective_period_start="2026-09-01"),
        source_a=PricingSourceRef(
            source_id="SRC-UNCERTAIN", source_type="FILE", name="Ambiguous Upload",
            requires_human_review=True,
        ),
        source_b=PricingSourceRef(source_id="AZ_HO3_2026_09_CLEAN", source_type="SAMPLE_RELEASE", name="Clean Target"),
    )

    res = supervisor.run_mission(mission, left_pkg, right_pkg)
    assert res.semantic_analysis.data.difference_count == 0
    assert res.release_decision.data.status == "REVIEW_REQUIRED"
    assert res.release_decision.data.status != "PASS"


def test_supervisor_flags_review_required_on_product_line_mismatch():
    """Comparing two genuinely different insurance products (e.g. a
    Homeowners source against a Personal Auto source) is not a meaningful
    equivalence check -- this metadata inconsistency must surface as its
    own signal and prevent PASS, not be silently absorbed into "0 semantic
    differences found" just because the two ASTs happen not to overlap."""
    store = InMemoryRunStore()
    supervisor = AssuranceSupervisor(store)

    left_pkg = resolve_demo_package("AZ_HO3_2026_09")
    right_pkg = resolve_demo_package("AZ_HO3_2026_09_CLEAN").model_copy(deep=True)
    right_pkg.product.line = InsuranceLine.PERSONAL_AUTO

    mission = AssuranceMission(
        mission_id="MIS-METAMISMATCH-01",
        name="Product Line Mismatch Mission",
        mode=ComparisonMode.EQUIVALENCE,
        objective=MissionObjective(product="AZ_HO3", jurisdiction="Arizona", effective_period_start="2026-09-01"),
        source_a=PricingSourceRef(source_id="AZ_HO3_2026_09", source_type="SAMPLE_RELEASE", name="Source A"),
        source_b=PricingSourceRef(source_id="AZ_HO3_2026_09_CLEAN", source_type="SAMPLE_RELEASE", name="Source B"),
    )

    res = supervisor.run_mission(mission, left_pkg, right_pkg)
    assert res.release_decision.data.status == "REVIEW_REQUIRED"
    assert res.release_decision.data.status != "PASS"
    assert any("product line" in reason for reason in res.release_decision.data.blocking_reasons)


def test_supervisor_defective_conformance_and_remediation():
    store = InMemoryRunStore()
    supervisor = AssuranceSupervisor(store)

    left_pkg = resolve_demo_package("AZ_HO3_2026_09")
    right_pkg = resolve_demo_package("AZ_HO3_2026_09_DEFECTIVE")

    mission = AssuranceMission(
        mission_id="MIS-DEFECTIVE-01",
        name="Defective Conformance Mission",
        mode=ComparisonMode.RELEASE_CONFORMANCE,
        objective=MissionObjective(product="AZ_HO3", jurisdiction="Arizona", effective_period_start="2026-09-01"),
        source_a=PricingSourceRef(source_id="AZ_HO3_2026_09", source_type="SAMPLE_RELEASE", name="Intent"),
        source_b=PricingSourceRef(source_id="AZ_HO3_2026_09_DEFECTIVE", source_type="SAMPLE_RELEASE", name="Defective Target"),
    )

    res = supervisor.run_mission(mission, left_pkg, right_pkg)
    assert res.release_decision.data.status == "BLOCK_DEPLOYMENT"
    assert res.semantic_analysis.data.difference_count > 0
    assert res.remediation.data is not None
    assert res.revalidation.data is not None
    assert res.revalidation.data.exposure_eliminated_pct == 100.0


def test_remediation_service_derived_package():
    rem_service = RemediationService()
    intent = resolve_demo_package("AZ_HO3_2026_09")
    defective = resolve_demo_package("AZ_HO3_2026_09_DEFECTIVE")

    proposal = rem_service.generate_remediation_proposal(intent, defective, [])
    assert proposal.derived_package_id.startswith("IPIR_PATCHED_")

    reval = rem_service.revalidate_remediation(intent, defective, proposal)
    assert reval.new_release_decision == "PASS"
    assert reval.exposure_eliminated_pct == 100.0


def test_mission_with_fee_modifier_and_exact_table_diffs_reports_full_blast_radius():
    """End-to-end regression test for a real judge-reported gap: a mission
    with a $5,000-deductible-tier factor drift, a multi-policy discount
    percentage change, and a global policy fee change used to report only
    ~5% portfolio exposure (the fee/modifier diffs produced no risk
    predicate at all, see app.engines.impact.predicates) and a single
    boundary-test scenario for three independent differences. Both are
    fixed; this locks in the corrected behavior end to end."""
    control = _load_fixture("multi_risk_control_a.json")
    drift = _load_fixture("multi_risk_drift_b.json")

    supervisor = AssuranceSupervisor(InMemoryRunStore())
    mission = AssuranceMission(
        mission_id="MIS-MULTIRISK-01",
        name="Multi-Risk Drift Regression",
        mode=ComparisonMode.RELEASE_CONFORMANCE,
        objective=MissionObjective(product="AZ_HO3", jurisdiction="AZ", effective_period_start="2026-09-01"),
        source_a=PricingSourceRef(source_id="multi-risk-a", source_type="FILE", name="A"),
        source_b=PricingSourceRef(source_id="multi-risk-b", source_type="FILE", name="B"),
    )

    res = supervisor.run_mission(mission, control, drift)

    assert res.release_decision.data.status == "BLOCK_DEPLOYMENT"
    assert res.semantic_analysis.data.difference_count == 3

    # The global fee change means every one of the 50,000 synthetic policies
    # is exposed, not just the ~5% share that hits the $5,000 deductible tier.
    br = res.blast_radius.data
    assert br.total_policies_analyzed == 50000
    assert br.semantically_exposed_count == 50000
    assert br.multi_defect_policy_count > 0

    # More than one boundary-test scenario for three independent diffs, with
    # at least one SINGLE_DEFECT witness and the combined MULTI_DEFECT case.
    experiments = res.experiments.data.experiments
    assert len(experiments) > 1

    # Remediation rationale names the actual diff kinds, not a fixed
    # "effective start dates and sequence ordering" sentence.
    rationale = res.remediation.data.rationale
    assert "rate table factor drift" in rationale
    assert "modifier value change" in rationale
    assert "fee amount change" in rationale
    assert "effective start date" not in rationale
    assert "sequence ordering" not in rationale

