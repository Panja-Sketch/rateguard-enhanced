from app.ipir.common import EffectivePeriod
from app.ipir.product import Jurisdiction
from app.ipir.v0_2.compatibility import check_compatibility
from app.ipir.v0_2.envelope import ProductRefV2

from .conftest import make_minimal_package


def test_identical_packages_are_compatible():
    a = make_minimal_package(package_id="pkg_a")
    b = make_minimal_package(package_id="pkg_b")
    result = check_compatibility(a, b)
    assert result.compatible
    assert result.reasons == []


def test_product_line_mismatch_is_reported():
    a = make_minimal_package(package_id="pkg_a")
    b = make_minimal_package(
        package_id="pkg_b",
        product=ProductRefV2(
            product_id="az_ho3", line="PERSONAL_AUTO", jurisdiction=Jurisdiction(country="US", state_or_province="AZ"), currency="USD"
        ),
    )
    result = check_compatibility(a, b)
    assert not result.compatible
    assert any("Product line mismatch" in r for r in result.reasons)


def test_jurisdiction_mismatch_is_reported():
    a = make_minimal_package(package_id="pkg_a")
    b = make_minimal_package(
        package_id="pkg_b",
        product=ProductRefV2(
            product_id="az_ho3", line="HOMEOWNERS", jurisdiction=Jurisdiction(country="US", state_or_province="CA"), currency="USD"
        ),
    )
    result = check_compatibility(a, b)
    assert not result.compatible
    assert any("Jurisdiction mismatch" in r for r in result.reasons)


def test_currency_mismatch_is_reported():
    a = make_minimal_package(package_id="pkg_a")
    b = make_minimal_package(
        package_id="pkg_b",
        product=ProductRefV2(
            product_id="az_ho3", line="HOMEOWNERS", jurisdiction=Jurisdiction(country="US", state_or_province="AZ"), currency="CAD"
        ),
    )
    result = check_compatibility(a, b)
    assert not result.compatible
    assert any("Currency mismatch" in r for r in result.reasons)


def test_non_overlapping_effective_periods_reported():
    a = make_minimal_package(package_id="pkg_a", effective_period=EffectivePeriod(start="2026-01-01", end="2026-06-01"))
    b = make_minimal_package(package_id="pkg_b", effective_period=EffectivePeriod(start="2026-07-01", end=None))
    result = check_compatibility(a, b)
    assert not result.compatible
    assert any("do not overlap" in r for r in result.reasons)


def test_no_overlapping_transaction_types_reported():
    from app.ipir.enums import TransactionType

    a = make_minimal_package(package_id="pkg_a", transaction_types=[TransactionType.NEW_BUSINESS])
    b = make_minimal_package(package_id="pkg_b", transaction_types=[TransactionType.POLICY_CHANGE])
    result = check_compatibility(a, b)
    assert not result.compatible
    assert any("transaction types" in r for r in result.reasons)


def test_schema_major_version_mismatch_reported():
    a = make_minimal_package(package_id="pkg_a", schema_version="0.2.0")
    b = make_minimal_package(package_id="pkg_b", schema_version="0.2.5")
    # Same major (0) -- should still be compatible on this axis.
    result = check_compatibility(a, b)
    assert "Schema major version mismatch" not in "".join(result.reasons)


def test_never_returns_bare_boolean_for_mismatch():
    a = make_minimal_package(package_id="pkg_a")
    b = make_minimal_package(
        package_id="pkg_b",
        product=ProductRefV2(
            product_id="az_ho3", line="PERSONAL_AUTO", jurisdiction=Jurisdiction(country="US", state_or_province="AZ"), currency="USD"
        ),
    )
    result = check_compatibility(a, b)
    assert isinstance(result.reasons, list) and len(result.reasons) > 0
