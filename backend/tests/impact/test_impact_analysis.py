from pathlib import Path

from app.engines.diff import compare_packages
from app.engines.diff.enums import DifferenceType
from app.engines.impact import ImpactAnalyzer, PricingDependencyGraph
from app.ipir.enums import ComparisonOperator
from app.ipir.package import IPIRPackage


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


def _load_fixture(name: str) -> IPIRPackage:
    path = Path(__file__).resolve().parent.parent / "fixtures" / name
    return IPIRPackage.model_validate_json(path.read_text(encoding="utf-8"))


def test_01_dependency_graph_edges_and_descendants() -> None:
    canonical = load_canonical_package()
    graph = PricingDependencyGraph(canonical)

    desc_roof_input = graph.get_descendants("roof_age")
    assert "roof_age_factor" in desc_roof_input
    assert "final_policy_premium" in desc_roof_input

    desc_roof_table = graph.get_descendants("roof_age_factor")
    assert "gross_risk_premium" in desc_roof_table
    assert "total_policy_premium" in desc_roof_table
    assert "final_policy_premium" in desc_roof_table

    anc_output = graph.get_ancestors("final_policy_premium")
    assert "base_rate" in anc_output
    assert "territory" in anc_output
    assert "roof_age" in anc_output


def test_02_2d_table_multi_input_connection() -> None:
    canonical = load_canonical_package()
    graph = PricingDependencyGraph(canonical)

    anc_2d = graph.get_ancestors("territory_construction_adjustment")
    assert "territory" in anc_2d
    assert "construction_type" in anc_2d


def test_03_impact_analysis_downstream_and_paths() -> None:
    canonical = load_canonical_package()
    defective = load_defective_package()

    diff_res = compare_packages(canonical, defective)
    analyzer = ImpactAnalyzer()
    impact = analyzer.analyze(diff_res, canonical)

    assert "roof_age_factor" in impact.changed_nodes
    assert "final_policy_premium" in impact.affected_outputs
    assert len(impact.dependency_paths) > 0

    roof_outputs = [p[-1] for p in impact.dependency_paths if p[0] == "roof_age_factor"]
    assert any(out in impact.affected_outputs for out in roof_outputs)


def test_04_impact_predicates_derived_correctly() -> None:
    canonical = load_canonical_package()
    defective = load_defective_package()

    diff_res = compare_packages(canonical, defective)
    analyzer = ImpactAnalyzer()
    impact = analyzer.analyze(diff_res, canonical)

    roof_pred = next(
        p
        for p in impact.candidate_risk_predicates
        if "roof_age_factor" in p.id or "21..30" in p.id or "roof_age" in p.description
    )
    assert roof_pred is not None
    assert len(roof_pred.clauses) == 2

    c_gte = next(c for c in roof_pred.clauses if c.operator == ComparisonOperator.GTE)
    c_lte = next(c for c in roof_pred.clauses if c.operator == ComparisonOperator.LTE)
    assert c_gte.field == "roof_age"
    assert c_gte.value == 21
    assert c_lte.field == "roof_age"
    assert c_lte.value == 30


def test_05_defective_package_order_change_defects_produce_global_predicates() -> None:
    """Regression test: AZ_HO3_2026_09_DEFECTIVE's third defect
    (DEFECT_PREMIUM_SEQUENCE_DRIFT, see data/implementations/defective/
    DEFECT_MANIFEST.json) is an ORDER_CHANGE on a constraint and a fee --
    a type derive_predicate_from_difference previously had no branch for
    and silently dropped (returning None), so it never reached the
    portfolio blast-radius scan at all. It must now produce a predicate
    (global, since neither constraints nor fees carry an eligibility
    condition in the IPIR schema), not vanish from candidate_risk_predicates."""
    canonical = load_canonical_package()
    defective = load_defective_package()

    diff_res = compare_packages(canonical, defective)
    order_diffs = [d for d in diff_res.differences if d.difference_type == DifferenceType.ORDER_CHANGE]
    assert len(order_diffs) == 2  # policy_minimum (constraint) + policy_fee (fee)

    impact = ImpactAnalyzer().analyze(diff_res, canonical)
    order_diff_ids = {d.id for d in order_diffs}
    order_predicates = [p for p in impact.candidate_risk_predicates if p.id.replace("pred_", "") in order_diff_ids]

    assert len(order_predicates) == 2
    for pred in order_predicates:
        assert pred.clauses == []  # global -- matches every policy


def test_06_modifier_change_predicate_derived_from_eligibility() -> None:
    """A modifier value change (a discount/surcharge percentage) must only
    be treated as affecting the policies its own eligibility condition
    selects -- not every policy, and not silently dropped."""
    control = _load_fixture("multi_risk_control_a.json")
    drift = _load_fixture("multi_risk_drift_b.json")

    diff_res = compare_packages(control, drift)
    modifier_diffs = [d for d in diff_res.differences if d.difference_type == DifferenceType.MODIFIER_CHANGE]
    assert len(modifier_diffs) == 1
    assert modifier_diffs[0].node_id == "multi_policy_discount"

    impact = ImpactAnalyzer().analyze(diff_res, control)
    pred = next(p for p in impact.candidate_risk_predicates if p.id == f"pred_{modifier_diffs[0].id}")
    assert len(pred.clauses) == 1
    assert pred.clauses[0].field == "multi_policy"
    assert pred.clauses[0].operator == ComparisonOperator.EQ
    assert pred.clauses[0].value is True


def test_07_fee_change_predicate_is_global() -> None:
    """A fee amount change has no eligibility condition in the IPIR schema
    -- it applies to every policy that reaches the fee's calculation node,
    so its predicate must have no clauses (matches everyone), never None."""
    control = _load_fixture("multi_risk_control_a.json")
    drift = _load_fixture("multi_risk_drift_b.json")

    diff_res = compare_packages(control, drift)
    fee_diffs = [d for d in diff_res.differences if d.difference_type == DifferenceType.FEE_CHANGE]
    assert len(fee_diffs) == 1
    assert fee_diffs[0].node_id == "policy_fee"

    impact = ImpactAnalyzer().analyze(diff_res, control)
    pred = next(p for p in impact.candidate_risk_predicates if p.id == f"pred_{fee_diffs[0].id}")
    assert pred.clauses == []
