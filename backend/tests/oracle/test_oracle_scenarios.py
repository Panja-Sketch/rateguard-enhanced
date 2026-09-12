from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.engines.oracle import (
    EffectiveDateError,
    InputValidationError,
    PremiumOracle,
    RiskInput,
    TableLookupError,
)
from app.engines.oracle.rounding import round_decimal
from app.ipir.enums import RoundingMode, TableLookupType, TransactionType
from app.ipir.package import IPIRPackage
from app.ipir.tables import ExactMatch, RateTable, TableDimension, TableRow


def get_canonical_package() -> IPIRPackage:
    """Helper to load the canonical Arizona HO3 IPIR package."""
    root_dir = Path(__file__).resolve().parent.parent.parent.parent
    canonical_file = (
        root_dir / "data" / "implementations" / "canonical" / "AZ_HO3_2026_09_ipir.json"
    )
    with open(canonical_file, encoding="utf-8") as f:
        return IPIRPackage.model_validate_json(f.read())


def test_scenario_a_high_risk_canonical() -> None:
    """Scenario A: Full high-risk canonical calculation test."""
    package = get_canonical_package()
    oracle = PremiumOracle()

    risk = RiskInput(
        values={
            "territory": "T17",
            "roof_age": 24,
            "deductible": 1000,
            "protection_class": 6,
            "construction_type": "FRAME",
            "dwelling_limit": 400000,
            "multi_policy": True,
            "claims_free": True,
        }
    )

    result = oracle.calculate(
        package=package,
        risk=risk,
        effective_date=date(2026, 9, 15),
        transaction_type=TransactionType.NEW_BUSINESS,
    )

    # 1. Verify resolved factors
    assert result.resolved_values["territory_factor"] == Decimal("1.20")
    assert result.resolved_values["roof_age_factor"] == Decimal("1.35")
    assert result.resolved_values["deductible_factor"] == Decimal("1.00")
    assert result.resolved_values["protection_class_factor"] == Decimal("1.00")
    assert result.resolved_values["construction_factor"] == Decimal("1.10")
    assert result.resolved_values["dwelling_limit_factor"] == Decimal("1.15")
    assert result.resolved_values["territory_construction_adjustment"] == Decimal("1.08")

    # 2. Independently derived Decimal calculation in test
    base_rate = Decimal("650.00")
    f_terr = Decimal("1.20")
    f_roof = Decimal("1.35")
    f_ded = Decimal("1.00")
    f_prot = Decimal("1.00")
    f_const = Decimal("1.10")
    f_dwell = Decimal("1.15")
    f_2d = Decimal("1.08")

    expected_gross = base_rate * f_terr * f_roof * f_ded * f_prot * f_const * f_dwell * f_2d
    expected_gross = round_decimal(expected_gross, 2, RoundingMode.HALF_UP)  # 1438.61

    disc1_amt = round_decimal(expected_gross * Decimal("0.12"), 2, RoundingMode.HALF_UP)  # 172.63
    after_disc1 = expected_gross - disc1_amt  # 1265.98

    disc2_amt = round_decimal(after_disc1 * Decimal("0.05"), 2, RoundingMode.HALF_UP)  # 63.30
    after_disc2 = after_disc1 - disc2_amt  # 1202.68

    expected_after_min = max(after_disc2, Decimal("575.00"))  # 1202.68
    expected_final = expected_after_min + Decimal("25.00")  # 1227.68

    assert result.resolved_values["gross_risk_premium"] == expected_gross
    assert result.final_premium == expected_final
    assert result.final_premium == Decimal("1227.68")


def test_scenario_b_roof_boundary_20() -> None:
    """Scenario B: Roof age 20 boundary check."""
    package = get_canonical_package()
    oracle = PremiumOracle()

    risk = RiskInput(
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

    result = oracle.calculate(package=package, risk=risk, effective_date=date(2026, 9, 15))
    assert result.resolved_values["roof_age_factor"] == Decimal("1.10")


def test_scenario_c_roof_boundary_21() -> None:
    """Scenario C: Roof age 21 boundary check."""
    package = get_canonical_package()
    oracle = PremiumOracle()

    risk = RiskInput(
        values={
            "territory": "T07",
            "roof_age": 21,
            "deductible": 1000,
            "protection_class": 5,
            "construction_type": "MASONRY",
            "dwelling_limit": 300000,
            "multi_policy": False,
            "claims_free": False,
        }
    )

    result = oracle.calculate(package=package, risk=risk, effective_date=date(2026, 9, 15))
    assert result.resolved_values["roof_age_factor"] == Decimal("1.35")


def test_scenario_d_minimum_premium_activated() -> None:
    """Scenario D: Low-risk scenario activating minimum policy premium constraint."""
    package = get_canonical_package()
    oracle = PremiumOracle()

    # Very low risk inputs
    risk = RiskInput(
        values={
            "territory": "T01",  # 0.88
            "roof_age": 2,  # 0.90
            "deductible": 5000,  # 0.72
            "protection_class": 1,  # 0.92
            "construction_type": "SUPERIOR",  # 0.88
            "dwelling_limit": 150000,  # 0.85
            "multi_policy": True,  # 12% disc
            "claims_free": True,  # 5% disc
        }
    )

    result = oracle.calculate(package=package, risk=risk, effective_date=date(2026, 9, 15))

    # Pre-minimum premium will be below $575.00
    assert result.resolved_values["premium_after_discounts"] < Decimal("575.00")
    assert result.resolved_values["premium_after_minimum"] == Decimal("575.00")
    assert result.final_premium == Decimal("600.00")  # $575 minimum + $25 fee


def test_scenario_e_no_discounts() -> None:
    """Scenario E: Calculation with multi_policy=False and claims_free=False."""
    package = get_canonical_package()
    oracle = PremiumOracle()

    risk = RiskInput(
        values={
            "territory": "T07",
            "roof_age": 5,
            "deductible": 1000,
            "protection_class": 5,
            "construction_type": "MASONRY",
            "dwelling_limit": 300000,
            "multi_policy": False,
            "claims_free": False,
        }
    )

    result = oracle.calculate(package=package, risk=risk, effective_date=date(2026, 9, 15))
    gross = result.resolved_values["gross_risk_premium"]
    after_disc = result.resolved_values["premium_after_discounts"]
    assert after_disc == gross


def test_scenario_f_renewal_transaction() -> None:
    """Scenario F: Evaluation with RENEWAL transaction type."""
    package = get_canonical_package()
    oracle = PremiumOracle()

    risk = RiskInput(
        values={
            "territory": "T07",
            "roof_age": 5,
            "deductible": 1000,
            "protection_class": 5,
            "construction_type": "MASONRY",
            "dwelling_limit": 300000,
            "multi_policy": False,
            "claims_free": False,
        }
    )

    result = oracle.calculate(
        package=package,
        risk=risk,
        effective_date=date(2026, 9, 15),
        transaction_type=TransactionType.RENEWAL,
    )
    assert result.transaction_type == TransactionType.RENEWAL


def test_scenario_g_invalid_territory() -> None:
    """Scenario G: Invalid territory input raises InputValidationError."""
    package = get_canonical_package()
    oracle = PremiumOracle()

    risk = RiskInput(
        values={
            "territory": "INVALID_T99",
            "roof_age": 5,
            "deductible": 1000,
            "protection_class": 5,
            "construction_type": "FRAME",
            "dwelling_limit": 300000,
            "multi_policy": False,
            "claims_free": False,
        }
    )

    with pytest.raises(InputValidationError, match="not in allowed values"):
        oracle.calculate(package=package, risk=risk, effective_date=date(2026, 9, 15))


def test_scenario_h_missing_required_input() -> None:
    """Scenario H: Missing required input raises InputValidationError."""
    package = get_canonical_package()
    oracle = PremiumOracle()

    risk = RiskInput(
        values={
            "territory": "T01",
            # roof_age missing
            "deductible": 1000,
            "protection_class": 5,
            "construction_type": "FRAME",
            "dwelling_limit": 300000,
            "multi_policy": False,
            "claims_free": False,
        }
    )

    with pytest.raises(InputValidationError, match="Missing required rating input: 'roof_age'"):
        oracle.calculate(package=package, risk=risk, effective_date=date(2026, 9, 15))


def test_scenario_i_invalid_effective_date() -> None:
    """Scenario I: Effective date prior to package start raises EffectiveDateError."""
    package = get_canonical_package()
    oracle = PremiumOracle()

    risk = RiskInput(
        values={
            "territory": "T01",
            "roof_age": 5,
            "deductible": 1000,
            "protection_class": 5,
            "construction_type": "FRAME",
            "dwelling_limit": 300000,
            "multi_policy": False,
            "claims_free": False,
        }
    )

    with pytest.raises(EffectiveDateError, match="not active on calculation date"):
        oracle.calculate(package=package, risk=risk, effective_date=date(2025, 1, 1))


def test_scenario_j_2d_lookup() -> None:
    """Scenario J: 2D table lookup for specific territory/construction combinations."""
    package = get_canonical_package()
    oracle = PremiumOracle()

    risk_t17 = RiskInput(
        values={
            "territory": "T17",
            "roof_age": 5,
            "deductible": 1000,
            "protection_class": 5,
            "construction_type": "FRAME",
            "dwelling_limit": 300000,
            "multi_policy": False,
            "claims_free": False,
        }
    )
    res_t17 = oracle.calculate(package=package, risk=risk_t17, effective_date=date(2026, 9, 15))
    assert res_t17.resolved_values["territory_construction_adjustment"] == Decimal("1.08")

    risk_t01 = RiskInput(
        values={
            "territory": "T01",
            "roof_age": 5,
            "deductible": 1000,
            "protection_class": 5,
            "construction_type": "FRAME",
            "dwelling_limit": 300000,
            "multi_policy": False,
            "claims_free": False,
        }
    )
    res_t01 = oracle.calculate(package=package, risk=risk_t01, effective_date=date(2026, 9, 15))
    assert res_t01.resolved_values["territory_construction_adjustment"] == Decimal("1.00")


def test_scenario_k_decimal_rounding() -> None:
    """Scenario K: Decimal rounding verification."""
    assert round_decimal(Decimal("100.005"), 2, RoundingMode.HALF_UP) == Decimal("100.01")
    assert round_decimal(Decimal("100.004"), 2, RoundingMode.HALF_UP) == Decimal("100.00")
    assert round_decimal(Decimal("1.23456"), 4, RoundingMode.HALF_UP) == Decimal("1.2346")


def test_scenario_l_ambiguous_lookup_rejection() -> None:
    """Scenario L: Duplicate table rows trigger TableLookupError."""
    ambiguous_table = RateTable(
        id="dup_table",
        name="Duplicate Row Table",
        dimensions=[TableDimension(input_ref="territory", lookup_type=TableLookupType.EXACT)],
        rows=[
            TableRow(matches=[ExactMatch(value="T01")], value=Decimal("1.00")),
            TableRow(matches=[ExactMatch(value="T01")], value=Decimal("1.05")),
        ],
    )

    from app.engines.oracle.table_lookup import lookup_table

    with pytest.raises(TableLookupError, match="Ambiguous rate table lookup"):
        lookup_table(ambiguous_table, {"territory": "T01"})
