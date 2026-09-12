from datetime import date
from decimal import Decimal
from pathlib import Path

from app.engines.oracle import PremiumOracle, RiskInput
from app.engines.target import IPIRRatingTarget
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


def test_01_target_engine_executes_defective_package_independently() -> None:
    canonical = load_canonical_package()
    defective = load_defective_package()

    target_engine = IPIRRatingTarget(target_id="defective-target")
    oracle = PremiumOracle()

    risk_roof_21 = RiskInput(
        values={
            "territory": "T17",
            "roof_age": 21,
            "deductible": 1000,
            "protection_class": 6,
            "construction_type": "FRAME",
            "dwelling_limit": 400000,
            "multi_policy": True,
            "claims_free": True,
        }
    )

    oracle_res = oracle.calculate(canonical, risk_roof_21, effective_date=date(2026, 9, 15))
    target_res = target_engine.quote(defective, risk_roof_21, effective_date=date(2026, 9, 15))

    # Canonical Oracle resolves roof factor 1.35
    assert oracle_res.resolved_values["roof_age_factor"] == Decimal("1.35")

    # Defective Target Engine resolves roof factor 1.25
    assert target_res.resolved_values["roof_age_factor"] == Decimal("1.25")

    # Premiums diverge
    assert oracle_res.final_premium != target_res.final_premium


def test_02_target_engine_roof_20_matches_canonical() -> None:
    canonical = load_canonical_package()
    defective = load_defective_package()

    target_engine = IPIRRatingTarget(target_id="defective-target")
    oracle = PremiumOracle()

    risk_roof_20 = RiskInput(
        values={
            "territory": "T07",
            "roof_age": 20,
            "deductible": 1000,
            "protection_class": 5,
            "construction_type": "MASONRY",
            "dwelling_limit": 300000,
            "multi_policy": False,
            "claims_free": False,
        }
    )

    oracle_res = oracle.calculate(canonical, risk_roof_20, effective_date=date(2026, 9, 15))
    target_res = target_engine.quote(defective, risk_roof_20, effective_date=date(2026, 9, 15))

    assert oracle_res.resolved_values["roof_age_factor"] == Decimal("1.10")
    assert target_res.resolved_values["roof_age_factor"] == Decimal("1.10")
    assert oracle_res.final_premium == target_res.final_premium
