from decimal import Decimal
from pathlib import Path

from app.engines.diff import DifferenceSeverity, DifferenceType, compare_packages
from app.ipir.package import IPIRPackage
from app.ipir.tables import RangeMatch


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


def test_01_canonical_and_defective_files_exist_and_validate() -> None:
    canonical = load_canonical_package()
    defective = load_defective_package()

    assert canonical.id == "AZ_HO3_2026_09"
    assert defective.id == "AZ_HO3_2026_09_DEFECTIVE"

    roof_c = next(t for t in canonical.tables if t.id == "roof_age_factor")
    row_c = next(
        r
        for r in roof_c.rows
        if isinstance(r.matches[0], RangeMatch)
        and r.matches[0].minimum == Decimal("21")
        and r.matches[0].maximum == Decimal("30")
    )
    assert row_c.value == Decimal("1.35")

    roof_d = next(t for t in defective.tables if t.id == "roof_age_factor")
    row_d = next(
        r
        for r in roof_d.rows
        if isinstance(r.matches[0], RangeMatch)
        and r.matches[0].minimum == Decimal("21")
        and r.matches[0].maximum == Decimal("30")
    )
    assert row_d.value == Decimal("1.25")


def test_02_identical_package_comparison_returns_zero_diffs() -> None:
    canonical = load_canonical_package()
    res = compare_packages(canonical, canonical)
    assert res.semantically_equal is True
    assert res.difference_count == 0
    assert len(res.differences) == 0


def test_03_diff_detects_roof_factor_change() -> None:
    canonical = load_canonical_package()
    defective = load_defective_package()

    res = compare_packages(canonical, defective)
    assert res.semantically_equal is False

    roof_diff = next(d for d in res.differences if d.node_id == "roof_age_factor")
    assert roof_diff.difference_type == DifferenceType.TABLE_ROW_CHANGE
    assert roof_diff.left_value == Decimal("1.35")
    assert roof_diff.right_value == Decimal("1.25")
    assert roof_diff.severity == DifferenceSeverity.CRITICAL


def test_04_diff_detects_effective_date_drift() -> None:
    canonical = load_canonical_package()
    defective = load_defective_package()

    res = compare_packages(canonical, defective)
    date_diff = next(
        d
        for d in res.differences
        if (
            d.difference_type == DifferenceType.EFFECTIVE_DATE_CHANGE
            and d.node_id == "claims_free_discount"
        )
    )
    assert date_diff.left_value == "2026-09-01"
    assert date_diff.right_value == "2026-09-15"
    assert date_diff.severity == DifferenceSeverity.HIGH


def test_05_diff_detects_order_sequence_drift() -> None:
    canonical = load_canonical_package()
    defective = load_defective_package()

    res = compare_packages(canonical, defective)
    order_diffs = [d for d in res.differences if d.difference_type == DifferenceType.ORDER_CHANGE]
    assert len(order_diffs) >= 2


def test_06_bidirectional_symmetry_and_reversal() -> None:
    canonical = load_canonical_package()
    defective = load_defective_package()

    res_ab = compare_packages(canonical, defective)
    res_ba = compare_packages(defective, canonical)

    assert res_ab.difference_count == res_ba.difference_count

    roof_ab = next(d for d in res_ab.differences if d.node_id == "roof_age_factor")
    roof_ba = next(d for d in res_ba.differences if d.node_id == "roof_age_factor")

    assert roof_ab.left_value == roof_ba.right_value
    assert roof_ab.right_value == roof_ba.left_value
