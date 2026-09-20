"""Real-path probe generation: control cases + baseline + boundaries mined from
the compiled IPIR, with dedupe, deterministic order, limits, provenance and
fail-closed behaviour. Nothing here monkeypatches the generator."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.engines.diff.models import SemanticDiffResult
from app.engines.impact.models import ImpactAnalysis
from app.engines.oracle.calculator import PremiumOracleCalculator
from app.engines.testing import PricingTestPlanner
from app.engines.testing.package_probes import (
    ProbeOrigin,
    canonical_probe_key,
    generate_package_probes,
)
from app.ipir.v0_2.compat import lower_to_v0_1
from app.ipir.v0_2.control_cases import ControlCase
from app.ipir.v0_2.package import IPIRPackageV2

GOLDEN = Path(__file__).resolve().parents[3] / "data" / "implementations" / "v0_2" / "canonical" / "AZ_HO3_GOLDEN_ipir.json"


@pytest.fixture()
def golden_v2() -> IPIRPackageV2:
    return IPIRPackageV2.model_validate_json(GOLDEN.read_text(encoding="utf-8"))


@pytest.fixture()
def package(golden_v2):
    return lower_to_v0_1(golden_v2)


@pytest.fixture()
def cases(golden_v2):
    return list(golden_v2.control_cases)


def _values(result, field):
    return sorted({s.risk_values[field] for s in result.scenarios if field in s.risk_values})


def test_canonical_workbook_includes_a_probe_that_confirms_700(package, cases):
    result = generate_package_probes(package, cases)
    control = [s for s in result.scenarios if s.metadata["probe_origin"] == ProbeOrigin.CONTROL_CASE]
    assert len(control) == 1
    assert control[0].risk_values == {"roof_age": 25, "dwelling_limit": Decimal("300000.00")}
    assert control[0].effective_date == date(2026, 10, 1)
    assert PremiumOracleCalculator(package).calculate_policy_premium(
        control[0].risk_values, effective_date=control[0].effective_date
    ).final_premium == Decimal("700.00")
    assert control[0].metadata["provenance"]["control_case_id"] == "golden_case"
    assert control[0].metadata["provenance"]["expected_outputs"] == {"final_premium_output": "700.00"}


def test_boundary_probes_cover_neighbours_of_roof_age_21(package, cases):
    result = generate_package_probes(package, cases)
    roof_ages = _values(result, "roof_age")
    # roof_age >= 21 tier: 20, 21 and 22 must all be exercised, plus the other
    # table boundaries, and nothing below the declared minimum of 0.
    assert {20, 21, 22} <= set(roof_ages)
    assert {0, 10, 11} <= set(roof_ages)
    assert min(roof_ages) >= 0
    boundary_21 = [s for s in result.scenarios if s.risk_values["roof_age"] == 21 and s.metadata["probe_origin"] == "BOUNDARY"]
    assert boundary_21 and "roof_age_factor_table" in boundary_21[0].metadata["provenance"]["boundary_source"]


def test_non_target_fields_come_from_control_case_never_all_zero(package, cases):
    result = generate_package_probes(package, cases)
    assert result.scenarios
    for scenario in result.scenarios:
        assert scenario.risk_values["dwelling_limit"] == Decimal("300000.00")
        assert scenario.risk_values != {"roof_age": 0, "dwelling_limit": 0}


def test_every_probe_has_origin_date_and_provenance_and_is_executable(package, cases):
    result = generate_package_probes(package, cases)
    oracle = PremiumOracleCalculator(package)
    for scenario in result.scenarios:
        assert scenario.metadata["probe_origin"] in {o.value for o in ProbeOrigin}
        assert scenario.metadata["calculation_date"] == "2026-10-01"
        assert scenario.metadata["calculation_date_source"] in {"CONTROL_CASE", "PACKAGE_EFFECTIVE_START"}
        assert isinstance(scenario.metadata["provenance"], dict) and scenario.metadata["provenance"]
        oracle.calculate_policy_premium(scenario.risk_values, effective_date=scenario.effective_date)


def test_generation_is_deterministic_and_deduplicated(package, cases):
    first = generate_package_probes(package, cases)
    second = generate_package_probes(package, cases)
    assert [s.model_dump() for s in first.scenarios] == [s.model_dump() for s in second.scenarios]
    keys = [canonical_probe_key(s.risk_values, s.effective_date, s.transaction_type) for s in first.scenarios]
    assert len(keys) == len(set(keys))


def test_equivalent_control_case_and_boundary_probe_are_deduplicated(golden_v2, package):
    at_21 = ControlCase(
        case_id="edge_21", inputs={"roof_age": 21, "dwelling_limit": "300000.00"},
        expected_outputs={"final_premium_output": "700.00"}, tolerance=Decimal("0.00"),
    )
    result = generate_package_probes(package, [at_21])
    twenty_ones = [s for s in result.scenarios if s.risk_values["roof_age"] == 21]
    assert len(twenty_ones) == 1
    assert twenty_ones[0].metadata["probe_origin"] == "CONTROL_CASE"  # control case wins the tie


def test_probe_limit_is_respected_and_control_case_survives(package, cases):
    result = generate_package_probes(package, cases, limit=4)
    assert len(result.scenarios) == 4
    assert result.scenarios[0].metadata["probe_origin"] == "CONTROL_CASE"


def test_control_case_date_is_used_for_the_case_and_inherited_by_boundaries(package):
    dated = ControlCase(
        case_id="dated", inputs={"roof_age": 25, "dwelling_limit": "300000.00", "effective_date": "2026-11-01"},
        expected_outputs={"final_premium_output": "700.00"}, tolerance=Decimal("0.00"),
    )
    result = generate_package_probes(package, [dated])
    assert result.scenarios[0].effective_date == date(2026, 11, 1)
    assert result.scenarios[0].metadata["calculation_date_source"] == "CONTROL_CASE"
    assert all(s.effective_date == date(2026, 11, 1) for s in result.scenarios)


@pytest.mark.parametrize(
    "inputs, why",
    [
        ({"roof_age": 25, "dwelling_limit": "300000.00", "effective_date": "2026-09-15"}, "outside the package period"),
        ({"roof_age": 25, "dwelling_limit": "300000.00", "unknown_field": 1}, "unknown input"),
        ({"roof_age": -3, "dwelling_limit": "300000.00"}, "below the declared minimum"),
        ({"roof_age": 25}, "missing required input"),
        ({"roof_age": 25, "dwelling_limit": "300000.00", "effective_date": "garbage"}, "malformed date"),
    ],
)
def test_invalid_control_cases_are_skipped_and_reported_not_executed(package, inputs, why):
    bad = ControlCase(case_id="bad_case", inputs=inputs, expected_outputs={"final_premium_output": "1.00"}, tolerance=Decimal("0"))
    result = generate_package_probes(package, [bad])
    assert all(s.metadata["probe_origin"] != "CONTROL_CASE" for s in result.scenarios), why
    assert any(item["ref"] == "bad_case" for item in result.skipped), why


def test_no_valid_control_case_and_no_declared_defaults_fails_closed(package):
    """`dwelling_limit` has no declared bound/default, so no baseline may be
    fabricated (no silent all-zero policy) and no probe can be built."""
    result = generate_package_probes(package, [])
    assert result.scenarios == []
    assert any(item["origin"] == "BASELINE" for item in result.skipped)

    plan = PricingTestPlanner().generate_plan(
        package,
        SemanticDiffResult(left_package_id=package.id, right_package_id=package.id, left_version="1", right_version="1", differences=[]),
        ImpactAnalysis(package_id=package.id),
        control_cases=[],
    )
    assert plan.selected_count == 0
    assert plan.planning_metadata["no_executable_probe"] is True


def test_planner_combines_control_and_boundary_probes_with_ordered_ids(package, cases):
    empty_diff = SemanticDiffResult(
        left_package_id=package.id, right_package_id=package.id, left_version="1", right_version="1", differences=[]
    )
    plan = PricingTestPlanner().generate_plan(package, empty_diff, ImpactAnalysis(package_id=package.id), control_cases=cases)
    origins = [s.metadata["probe_origin"] for s in plan.selected_scenarios]
    assert origins[0] == "CONTROL_CASE" and "BOUNDARY" in origins
    assert [s.id for s in plan.selected_scenarios] == [f"RG-{i:03d}" for i in range(1, plan.selected_count + 1)]
    assert plan.coverage_metrics["probe_origin_counts"]["CONTROL_CASE"] == 1
    assert {20, 21, 22} <= {s.risk_values["roof_age"] for s in plan.selected_scenarios}


def test_planner_adds_mutation_probes_when_structural_differences_are_known():
    """With a real canonical-vs-defective diff the planner must still produce
    control/boundary probes AND mutation-targeted probes, each labelled."""
    from app.engines.diff import compare_packages
    from app.engines.impact import ImpactAnalyzer
    from app.ipir.package import IPIRPackage

    root = Path(__file__).resolve().parents[3] / "data" / "implementations"
    canonical = IPIRPackage.model_validate_json((root / "canonical" / "AZ_HO3_2026_09_ipir.json").read_text(encoding="utf-8"))
    defective = IPIRPackage.model_validate_json((root / "defective" / "AZ_HO3_2026_09_ipir.json").read_text(encoding="utf-8"))
    diff = compare_packages(canonical, defective)
    plan = PricingTestPlanner().generate_plan(canonical, diff, ImpactAnalyzer().analyze(diff, canonical))

    origins = {s.metadata["probe_origin"] for s in plan.selected_scenarios}
    assert "MUTATION" in origins and origins & {"BASELINE", "CONTROL_CASE"} and "BOUNDARY" in origins
    assert plan.selected_count <= 15
    keys = [canonical_probe_key(s.risk_values, s.effective_date, s.transaction_type) for s in plan.selected_scenarios]
    assert len(keys) == len(set(keys))
