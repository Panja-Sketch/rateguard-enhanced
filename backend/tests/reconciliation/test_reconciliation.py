from datetime import date
from decimal import Decimal
from pathlib import Path

from app.engines.diff import DifferenceType, compare_packages
from app.engines.oracle import PremiumOracle, RiskInput
from app.engines.reconciliation import (
    PricingAssuranceRunner,
    ReconciliationEngine,
)
from app.engines.target import IPIRRatingTarget
from app.engines.testing import PricingTestScenario
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


def test_01_reconciliation_roof_20_control_matches() -> None:
    canonical = load_canonical_package()
    defective = load_defective_package()
    diff_res = compare_packages(canonical, defective)

    oracle = PremiumOracle()
    target_engine = IPIRRatingTarget()
    reconciler = ReconciliationEngine()

    scenario = PricingTestScenario(
        id="RG-CTRL",
        name="Roof 20 Control Test",
        risk_values={
            "territory": "T07",
            "roof_age": 20,
            "deductible": 1000,
            "protection_class": 5,
            "construction_type": "MASONRY",
            "dwelling_limit": 500000,
            "multi_policy": False,
            "claims_free": False,
        },
        effective_date=date(2026, 9, 20),
        purpose="Control test baseline",
    )

    risk_in = RiskInput(values=scenario.risk_values)
    exp = oracle.calculate(canonical, risk_in, scenario.effective_date)
    act = target_engine.quote(defective, risk_in, scenario.effective_date)

    recon = reconciler.reconcile(scenario, exp, act, diff_res, canonical)

    assert recon.premium_matches is True
    assert recon.status == "MATCH"
    assert recon.absolute_variance == Decimal("0.00")


def test_02_reconciliation_roof_21_mismatch_identifies_upstream_rca() -> None:
    canonical = load_canonical_package()
    defective = load_defective_package()
    diff_res = compare_packages(canonical, defective)

    oracle = PremiumOracle()
    target_engine = IPIRRatingTarget()
    reconciler = ReconciliationEngine()

    scenario = PricingTestScenario(
        id="RG-MISMATCH",
        name="Roof 21 Mismatch Test",
        risk_values={
            "territory": "T17",
            "roof_age": 24,
            "deductible": 1000,
            "protection_class": 6,
            "construction_type": "FRAME",
            "dwelling_limit": 400000,
            "multi_policy": True,
            "claims_free": True,
        },
        effective_date=date(2026, 9, 20),
        purpose="Targeted defect test",
    )

    risk_in = RiskInput(values=scenario.risk_values)
    exp = oracle.calculate(canonical, risk_in, scenario.effective_date)
    act = target_engine.quote(defective, risk_in, scenario.effective_date)

    recon = reconciler.reconcile(scenario, exp, act, diff_res, canonical)

    assert recon.premium_matches is False
    assert recon.status == "MISMATCH"
    assert recon.first_divergent_node == "roof_age_factor"
    assert recon.root_cause is not None
    assert recon.root_cause.difference_type == DifferenceType.TABLE_ROW_CHANGE
    assert recon.root_cause.expected_value == Decimal("1.35")
    assert recon.root_cause.actual_value == Decimal("1.25")


def test_03_full_pricing_assurance_runner_pipeline() -> None:
    canonical = load_canonical_package()
    defective = load_defective_package()

    runner = PricingAssuranceRunner()
    run = runner.run_assurance(canonical, defective)

    assert run.overall_status == "FAIL"
    assert run.passed_count > 0
    assert run.failed_count > 0
    assert run.semantic_difference_coverage_pct == 100.0
    assert run.behavioral_difference_coverage_pct > 0.0
    assert run.premium_difference_reproduction_rate_pct > 0.0
    assert len(run.discovered_root_causes) > 0
