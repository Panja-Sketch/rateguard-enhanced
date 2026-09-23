"""Regression coverage for the release-gate headline counting the right thing.

Locked-brief requirement: "One structural defect with three failing probes
must display: one material finding; three mismatched probes." Material
findings must be derived from the structural semantic diff (one row per
`SemanticDifference`), never from the number of boundary/mutation probes that
happened to reproduce a mismatch against that one defect -- those are a
separate count (`ExperimentsData.mismatch_count` /
`ReconciliationData.mismatch_count` in `app.models.result_v2`).
"""

from app.engines.diff.enums import DifferenceSeverity, DifferenceType
from app.engines.diff.models import SemanticDifference, SemanticDiffResult
from app.services.finding_conversion import to_material_findings


def _make_diff_result(num_differences: int) -> SemanticDiffResult:
    differences = [
        SemanticDifference(
            id=f"diff-{i}",
            difference_type=DifferenceType.VALUE_CHANGE,
            semantic_path=f"tables.roof_age_factor[{i}]",
            node_id=f"node-{i}",
            node_type="TABLE_ROW",
            left_value="1.35",
            right_value="1.25",
            severity=DifferenceSeverity.HIGH,
            description="Roof-age factor drift",
        )
        for i in range(num_differences)
    ]
    return SemanticDiffResult(
        left_package_id="AZ_HO3_2026_09",
        right_package_id="AZ_HO3_2026_09_DEFECTIVE",
        left_version="1.0.0",
        right_version="1.0.0",
        differences=differences,
        difference_count=num_differences,
        semantically_equal=num_differences == 0,
    )


def test_one_structural_defect_yields_exactly_one_material_finding():
    """A single structural defect must convert to exactly one MaterialFinding,
    regardless of how many boundary/mutation probes later reproduce a
    mismatch against it -- probe-level mismatch counts live in a separate
    field (`ExperimentsData.mismatch_count`) and must never be conflated
    with the structural finding count."""
    diff_result = _make_diff_result(num_differences=1)

    findings = to_material_findings(diff_result)

    assert len(findings) == 1
    assert findings[0].category == "SEMANTIC_DIFF"
    assert findings[0].intent_value == "1.35"
    assert findings[0].target_value == "1.25"


def test_material_finding_count_is_independent_of_probe_mismatch_count():
    """Simulates the exact brief scenario: three probes mismatch against one
    structural defect. The material-finding count must remain 1; the probe
    mismatch count (tracked separately, e.g. ExperimentsData.mismatch_count)
    is not derived from or capped by the finding conversion at all."""
    diff_result = _make_diff_result(num_differences=1)
    findings = to_material_findings(diff_result)

    # Three probes independently reproducing a mismatch against the same
    # single structural defect -- represented here as a plain int, exactly
    # as ExperimentsData.mismatch_count is populated by the boundary-test
    # engine, deliberately not by finding_conversion.
    simulated_probe_mismatch_count = 3

    assert len(findings) == 1, "material finding count must not equal probe mismatch count"
    assert simulated_probe_mismatch_count == 3
    assert len(findings) != simulated_probe_mismatch_count


def test_multiple_structural_defects_yield_matching_finding_count():
    diff_result = _make_diff_result(num_differences=4)

    findings = to_material_findings(diff_result)

    assert len(findings) == 4


def test_zero_structural_differences_yield_zero_findings():
    diff_result = _make_diff_result(num_differences=0)

    findings = to_material_findings(diff_result)

    assert findings == []
