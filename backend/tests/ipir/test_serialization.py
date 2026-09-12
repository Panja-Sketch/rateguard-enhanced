from decimal import Decimal
from pathlib import Path

from app.ipir.package import IPIRPackage


def test_minimal_fixture_deserialization() -> None:
    fixture_path = Path(__file__).resolve().parent.parent / "fixtures" / "minimal_ipir.json"
    with open(fixture_path, encoding="utf-8") as f:
        json_str = f.read()

    package = IPIRPackage.model_validate_json(json_str)
    assert package.id == "minimal_rate_plan"
    assert package.product.line == "HOMEOWNERS"
    assert len(package.inputs) == 1
    assert len(package.constants) == 1
    assert package.constants[0].value == Decimal("500.00")
    assert isinstance(package.constants[0].value, Decimal)


def test_decimal_serialization_round_trip() -> None:
    fixture_path = Path(__file__).resolve().parent.parent / "fixtures" / "minimal_ipir.json"
    with open(fixture_path, encoding="utf-8") as f:
        original_json = f.read()

    package = IPIRPackage.model_validate_json(original_json)

    # Serialize back to JSON string
    reserialized_json = package.model_dump_json()

    # Re-deserialize from reserialized JSON string
    package_reloaded = IPIRPackage.model_validate_json(reserialized_json)

    # Verify complete semantic equivalence and Decimal preservation
    assert package_reloaded.id == package.id
    assert package_reloaded.constants[0].value == Decimal("500.00")
    assert package_reloaded.tables[0].rows[2].value == Decimal("1.35")
    assert isinstance(package_reloaded.tables[0].rows[2].value, Decimal)
