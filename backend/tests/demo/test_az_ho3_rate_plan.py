from datetime import date
from decimal import Decimal
from pathlib import Path

from app.ipir.enums import (
    ConstraintType,
    InsuranceLine,
    ModifierType,
    ProvenanceSourceType,
    TransactionType,
)
from app.ipir.package import IPIRPackage
from app.ipir.tables import ExactMatch, RangeMatch


def get_canonical_file_path() -> Path:
    """Helper to locate the canonical IPIR package file."""
    root_dir = Path(__file__).resolve().parent.parent.parent.parent
    return root_dir / "data" / "implementations" / "canonical" / "AZ_HO3_2026_09_ipir.json"


def load_canonical_package() -> IPIRPackage:
    """Helper to load and parse the canonical IPIR package."""
    file_path = get_canonical_file_path()
    with open(file_path, encoding="utf-8") as f:
        return IPIRPackage.model_validate_json(f.read())


def test_01_canonical_rate_package_loads() -> None:
    pkg = load_canonical_package()
    assert pkg is not None


def test_02_package_id() -> None:
    pkg = load_canonical_package()
    assert pkg.id == "AZ_HO3_2026_09"


def test_03_state_is_az() -> None:
    pkg = load_canonical_package()
    assert pkg.product.jurisdiction.state_or_province == "AZ"
    assert pkg.product.jurisdiction.country == "US"


def test_04_line_is_homeowners() -> None:
    pkg = load_canonical_package()
    assert pkg.product.line == InsuranceLine.HOMEOWNERS


def test_05_form_is_ho3() -> None:
    pkg = load_canonical_package()
    assert pkg.product.form == "HO3"


def test_06_effective_date() -> None:
    pkg = load_canonical_package()
    assert pkg.effective_period.start == date(2026, 9, 1)
    assert pkg.effective_period.end is None


def test_07_roof_age_21_to_30_factor() -> None:
    pkg = load_canonical_package()
    roof_table = next(t for t in pkg.tables if t.id == "roof_age_factor")
    row_21_30 = next(
        r
        for r in roof_table.rows
        if isinstance(r.matches[0], RangeMatch)
        and r.matches[0].minimum == Decimal("21")
        and r.matches[0].maximum == Decimal("30")
    )
    assert row_21_30.value == Decimal("1.35")
    assert isinstance(row_21_30.value, Decimal)


def test_08_territory_t17_factor() -> None:
    pkg = load_canonical_package()
    t_table = next(t for t in pkg.tables if t.id == "territory_factor")
    row_t17 = next(
        r
        for r in t_table.rows
        if isinstance(r.matches[0], ExactMatch) and r.matches[0].value == "T17"
    )
    assert row_t17.value == Decimal("1.20")


def test_09_deductible_1000_factor() -> None:
    pkg = load_canonical_package()
    d_table = next(t for t in pkg.tables if t.id == "deductible_factor")
    row_1000 = next(
        r
        for r in d_table.rows
        if isinstance(r.matches[0], ExactMatch) and r.matches[0].value == "1000"
    )
    assert row_1000.value == Decimal("1.00")


def test_10_construction_frame_factor() -> None:
    pkg = load_canonical_package()
    c_table = next(t for t in pkg.tables if t.id == "construction_factor")
    row_frame = next(
        r
        for r in c_table.rows
        if isinstance(r.matches[0], ExactMatch) and r.matches[0].value == "FRAME"
    )
    assert row_frame.value == Decimal("1.10")


def test_11_multi_policy_discount() -> None:
    pkg = load_canonical_package()
    mod = next(m for m in pkg.modifiers if m.id == "multi_policy_discount")
    assert mod.modifier_type == ModifierType.PERCENTAGE_DISCOUNT
    assert mod.value == Decimal("0.12")


def test_12_claims_free_discount() -> None:
    pkg = load_canonical_package()
    mod = next(m for m in pkg.modifiers if m.id == "claims_free_discount")
    assert mod.modifier_type == ModifierType.PERCENTAGE_DISCOUNT
    assert mod.value == Decimal("0.05")


def test_13_minimum_premium() -> None:
    pkg = load_canonical_package()
    constraint = next(c for c in pkg.constraints if c.id == "policy_minimum")
    assert constraint.constraint_type == ConstraintType.MINIMUM
    assert constraint.amount == Decimal("575.00")


def test_14_policy_fee() -> None:
    pkg = load_canonical_package()
    fee = next(f for f in pkg.fees if f.id == "policy_fee")
    assert fee.amount == Decimal("25.00")


def test_15_2d_table_exists() -> None:
    pkg = load_canonical_package()
    tc_table = next(t for t in pkg.tables if t.id == "territory_construction_adjustment")
    assert len(tc_table.dimensions) == 2
    assert len(tc_table.rows) == 60


def test_16_t17_frame_factor() -> None:
    pkg = load_canonical_package()
    tc_table = next(t for t in pkg.tables if t.id == "territory_construction_adjustment")
    row = next(
        r
        for r in tc_table.rows
        if isinstance(r.matches[0], ExactMatch)
        and r.matches[0].value == "T17"
        and isinstance(r.matches[1], ExactMatch)
        and r.matches[1].value == "FRAME"
    )
    assert row.value == Decimal("1.08")


def test_17_no_duplicate_semantic_ids() -> None:
    pkg = load_canonical_package()
    all_ids = [
        *[inp.id for inp in pkg.inputs],
        *[c.id for c in pkg.constants],
        *[t.id for t in pkg.tables],
        *[r.id for r in pkg.rules],
        *[calc.id for calc in pkg.calculations],
        *[m.id for m in pkg.modifiers],
        *[c.id for c in pkg.constraints],
        *[f.id for f in pkg.fees],
        *[cov.id for cov in pkg.coverages],
        *[out.id for out in pkg.outputs],
    ]
    assert len(all_ids) == len(set(all_ids))


def test_18_package_serialization_round_trip() -> None:
    pkg = load_canonical_package()
    json_str = pkg.model_dump_json()
    pkg_reloaded = IPIRPackage.model_validate_json(json_str)

    assert pkg_reloaded.id == pkg.id
    assert pkg_reloaded.constants[0].value == Decimal("650.00")
    roof_table = next(t for t in pkg_reloaded.tables if t.id == "roof_age_factor")
    assert roof_table.rows[3].value == Decimal("1.35")
    assert isinstance(roof_table.rows[3].value, Decimal)


def test_19_provenance_identifies_actuarial_spec() -> None:
    pkg = load_canonical_package()
    assert pkg.provenance is not None
    assert len(pkg.provenance.sources) == 1
    source = pkg.provenance.sources[0]
    assert source.source_type == ProvenanceSourceType.ACTUARIAL_SPEC
    assert source.source_id == "synthetic-az-ho3-2026-09"
    assert pkg.provenance.extraction_confidence == Decimal("1.0")


def test_20_transaction_types() -> None:
    pkg = load_canonical_package()
    assert TransactionType.NEW_BUSINESS in pkg.transaction_types
    assert TransactionType.RENEWAL in pkg.transaction_types


# --- SANITY CHECKS ---


def test_sanity_roof_age_ranges_no_overlap() -> None:
    pkg = load_canonical_package()
    table = next(t for t in pkg.tables if t.id == "roof_age_factor")
    ranges = [r.matches[0] for r in table.rows if isinstance(r.matches[0], RangeMatch)]

    for i in range(len(ranges) - 1):
        r1, r2 = ranges[i], ranges[i + 1]
        assert r1.maximum is not None and r2.minimum is not None
        assert Decimal(str(r1.maximum)) < Decimal(str(r2.minimum))


def test_sanity_protection_class_ranges_cover_1_to_10() -> None:
    pkg = load_canonical_package()
    table = next(t for t in pkg.tables if t.id == "protection_class_factor")
    ranges = [r.matches[0] for r in table.rows if isinstance(r.matches[0], RangeMatch)]

    assert ranges[0].minimum == Decimal("1")
    assert ranges[-1].maximum == Decimal("10")

    for i in range(len(ranges) - 1):
        assert ranges[i].maximum is not None and ranges[i + 1].minimum is not None
        assert Decimal(str(ranges[i].maximum)) + 1 == Decimal(str(ranges[i + 1].minimum))


def test_sanity_territories_all_20_present() -> None:
    pkg = load_canonical_package()
    table = next(t for t in pkg.tables if t.id == "territory_factor")
    territories = [r.matches[0].value for r in table.rows if isinstance(r.matches[0], ExactMatch)]
    expected_territories = [f"T{i:02d}" for i in range(1, 21)]
    assert territories == expected_territories


def test_sanity_construction_types_present() -> None:
    pkg = load_canonical_package()
    table = next(t for t in pkg.tables if t.id == "construction_factor")
    ctypes = [r.matches[0].value for r in table.rows if isinstance(r.matches[0], ExactMatch)]
    assert all(c in ctypes for c in ["FRAME", "MASONRY", "SUPERIOR"])


def test_sanity_2d_table_60_combinations() -> None:
    pkg = load_canonical_package()
    table = next(t for t in pkg.tables if t.id == "territory_construction_adjustment")
    combos = set()
    for row in table.rows:
        t_val = row.matches[0].value
        c_val = row.matches[1].value
        combos.add((t_val, c_val))

    assert len(combos) == 60
