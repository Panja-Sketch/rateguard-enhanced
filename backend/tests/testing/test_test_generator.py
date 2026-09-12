from pathlib import Path

from app.engines.diff import compare_packages
from app.engines.impact import ImpactAnalyzer
from app.engines.testing import PricingTestPlanner, generate_range_boundary_values
from app.engines.testing.models import ScenarioClassification
from app.ipir.package import IPIRPackage


def _load_fixture(name: str) -> IPIRPackage:
    path = Path(__file__).resolve().parent.parent / "fixtures" / name
    return IPIRPackage.model_validate_json(path.read_text(encoding="utf-8"))


def get_canonical_file_path() -> Path:
    root_dir = Path(__file__).resolve().parent.parent.parent.parent
    return root_dir / "data" / "implementations" / "canonical" / "AZ_HO3_2026_09_ipir.json"


def get_defective_file_path() -> Path:
    root_dir = Path(__file__).resolve().parent.parent.parent.parent
    return root_dir / "data" / "implementations" / "defective" / "AZ_HO3_2026_09_ipir.json"


def load_canonical_package() -> IPIRPackage:
    with open(get_canonical_file_path(), encoding="utf-8") as f:
        return IPIRPackage.model_validate_json(f.read())


def load_defective_package() -> IPIRPackage:
    with open(get_defective_file_path(), encoding="utf-8") as f:
        return IPIRPackage.model_validate_json(f.read())


def test_01_boundary_values_generation() -> None:
    bounds = generate_range_boundary_values(21, 30)
    assert bounds["just_below"] == 20
    assert bounds["lower_bound"] == 21
    assert bounds["interior"] == 25
    assert bounds["upper_bound"] == 30
    assert bounds["just_above"] == 31


def test_02_test_plan_generation_and_optimization() -> None:
    canonical = load_canonical_package()
    defective = load_defective_package()

    diff_res = compare_packages(canonical, defective)
    impact = ImpactAnalyzer().analyze(diff_res, canonical)

    planner = PricingTestPlanner()
    plan = planner.generate_plan(canonical, diff_res, impact)

    assert plan.candidate_count > 0
    assert plan.selected_count > 0
    assert plan.selected_count <= plan.candidate_count
    assert plan.coverage_metrics["semantic_difference_coverage_pct"] == 100.0
    assert plan.coverage_metrics["candidate_reduction_pct"] >= 0.0

    # Verify roof boundary coverage scenarios exist in selected plan
    scenarios = plan.selected_scenarios
    roof_values = [s.risk_values.get("roof_age") for s in scenarios if "roof_age" in s.risk_values]
    assert 20 in roof_values
    assert 21 in roof_values
    assert 30 in roof_values or 31 in roof_values


def test_03_isolated_and_combined_scenarios_for_exact_match_and_global_diffs() -> None:
    """Regression test for a candidate pool that used to generate exactly
    one scenario (the baseline control) for three independent, non-range
    differences (an EXACT-match table row, a modifier value change, and a
    fee amount change) -- range-boundary generation is the only diff shape
    generate_candidate_scenarios ever built isolated coverage for. It must
    now produce a SINGLE_DEFECT witness for each EXACT/modifier-eligibility
    predicate and a MULTI_DEFECT scenario combining them."""
    control = _load_fixture("multi_risk_control_a.json")
    drift = _load_fixture("multi_risk_drift_b.json")

    diff_res = compare_packages(control, drift)
    assert diff_res.difference_count == 3

    impact = ImpactAnalyzer().analyze(diff_res, control)
    planner = PricingTestPlanner()
    plan = planner.generate_plan(control, diff_res, impact)

    classifications = [sc.classification for sc in plan.candidate_scenarios]
    assert ScenarioClassification.SINGLE_DEFECT in classifications
    assert ScenarioClassification.MULTI_DEFECT in classifications

    single_defect = [sc for sc in plan.candidate_scenarios if sc.classification == ScenarioClassification.SINGLE_DEFECT]
    assert len(single_defect) == 2  # deductible=5000 alone, multi_policy=True alone

    deductible_witness = next(sc for sc in single_defect if sc.risk_values.get("deductible") == "5000")
    assert deductible_witness.risk_values.get("multi_policy") is not True

    multi_policy_witness = next(sc for sc in single_defect if sc.risk_values.get("multi_policy") is True)
    assert multi_policy_witness.risk_values.get("deductible") != "5000"

    combined = next(sc for sc in plan.candidate_scenarios if sc.classification == ScenarioClassification.MULTI_DEFECT)
    assert combined.risk_values.get("deductible") == "5000"
    assert combined.risk_values.get("multi_policy") is True
