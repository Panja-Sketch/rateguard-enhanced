"""Deterministic calculation-date resolution (explicit > control case > package
start, fail closed otherwise) and proof that no static date remains in the real
mission path."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.engines.oracle.calculation_date import CalculationDateSource, resolve_calculation_date
from app.engines.oracle.calculator import PremiumOracleCalculator
from app.engines.oracle.errors import CalculationDateError
from app.ipir.common import EffectivePeriod
from app.ipir.v0_2.compat import lower_to_v0_1
from app.ipir.v0_2.package import IPIRPackageV2

REPO_ROOT = Path(__file__).resolve().parents[3]
GOLDEN = REPO_ROOT / "data" / "implementations" / "v0_2" / "canonical" / "AZ_HO3_GOLDEN_ipir.json"
GOLDEN_INPUTS = {"roof_age": 25, "dwelling_limit": "300000.00"}


@pytest.fixture()
def package():
    """The locked golden package: effective 2026-10-01, open-ended."""
    return lower_to_v0_1(IPIRPackageV2.model_validate_json(GOLDEN.read_text(encoding="utf-8")))


def test_package_starting_2026_10_01_uses_2026_10_01(package):
    assert package.effective_period.start == date(2026, 10, 1)
    resolved = resolve_calculation_date(package)
    assert resolved.value == date(2026, 10, 1)
    assert resolved.source == CalculationDateSource.PACKAGE_EFFECTIVE_START

    result = PremiumOracleCalculator(package).calculate_policy_premium(dict(GOLDEN_INPUTS))
    assert result.calculation_date == date(2026, 10, 1)
    assert result.calculation_date_source == CalculationDateSource.PACKAGE_EFFECTIVE_START
    assert result.final_premium == Decimal("700.00")


def test_explicit_valid_date_takes_precedence_over_control_case_and_package(package):
    resolved = resolve_calculation_date(package, explicit="2026-11-15", control_case="2026-10-05")
    assert resolved.value == date(2026, 11, 15)
    assert resolved.source == CalculationDateSource.EXPLICIT


def test_explicit_date_can_be_carried_in_probe_risk_values(package):
    calc = PremiumOracleCalculator(package)
    result = calc.calculate_policy_premium({**GOLDEN_INPUTS, "effective_date": "2026-12-01"})
    assert result.calculation_date == date(2026, 12, 1)
    assert result.calculation_date_source == CalculationDateSource.EXPLICIT


def test_control_case_date_precedes_package_start(package):
    resolved = resolve_calculation_date(package, control_case=date(2026, 10, 5))
    assert resolved.value == date(2026, 10, 5)
    assert resolved.source == CalculationDateSource.CONTROL_CASE


@pytest.mark.parametrize("bad", ["2026-09-15", "2026-09-30", date(2020, 1, 1)])
def test_date_before_package_start_is_rejected_with_specific_code(package, bad):
    with pytest.raises(CalculationDateError) as exc:
        resolve_calculation_date(package, explicit=bad)
    assert exc.value.code == "CALCULATION_DATE_OUT_OF_PERIOD"
    with pytest.raises(CalculationDateError) as exc2:
        PremiumOracleCalculator(package).calculate_policy_premium(dict(GOLDEN_INPUTS), effective_date=bad)
    assert exc2.value.code == "CALCULATION_DATE_OUT_OF_PERIOD"


def test_date_after_package_end_is_rejected(package):
    bounded = package.model_copy(update={"effective_period": EffectivePeriod(start=date(2026, 10, 1), end=date(2026, 12, 31))})
    assert resolve_calculation_date(bounded, explicit="2026-12-31").value == date(2026, 12, 31)
    with pytest.raises(CalculationDateError) as exc:
        resolve_calculation_date(bounded, explicit="2027-01-01")
    assert exc.value.code == "CALCULATION_DATE_OUT_OF_PERIOD"


@pytest.mark.parametrize("bad", ["not-a-date", "2026-13-40", "", 20261001])
def test_malformed_explicit_date_fails_closed_and_never_falls_back(package, bad):
    with pytest.raises(CalculationDateError) as exc:
        resolve_calculation_date(package, explicit=bad, control_case="2026-10-05")
    assert exc.value.code == "INVALID_CALCULATION_DATE"


def test_no_package_and_no_date_fails_closed_without_substituting_a_constant():
    with pytest.raises(CalculationDateError) as exc:
        resolve_calculation_date(None)
    assert exc.value.code == "MISSING_CALCULATION_DATE"


def test_no_static_calculation_date_remains_in_the_real_mission_path():
    """The removed hard-coded default must not reappear anywhere in the engines,
    supervisor, or services that run a real mission (the connector health
    check's own locked golden date is a documented, separate concern)."""
    static_date = re.compile(r"2026-09-15|date\(\s*2026\s*,\s*0?9\s*,\s*15\s*\)")
    offenders = []
    for folder in ("engines", "agents", "services", "api", "missions", "models"):
        root = REPO_ROOT / "backend" / "app" / folder
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if static_date.search(path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == []
