from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.ipir.common import EffectivePeriod, LiteralValue, NodeReference


def test_effective_period_valid() -> None:
    period = EffectivePeriod(start=date(2026, 1, 1), end=date(2026, 12, 31))
    assert period.start == date(2026, 1, 1)
    assert period.end == date(2026, 12, 31)


def test_effective_period_invalid() -> None:
    with pytest.raises(ValidationError, match="End date .* cannot precede start date"):
        EffectivePeriod(start=date(2026, 12, 31), end=date(2026, 1, 1))


def test_node_reference_validation() -> None:
    ref = NodeReference(ref="base_rate_2026")
    assert ref.ref == "base_rate_2026"

    with pytest.raises(ValidationError, match="must contain only alphanumeric"):
        NodeReference(ref="invalid ID with spaces")


def test_literal_value_decimal_preservation() -> None:
    val = LiteralValue(value=Decimal("1.35"))
    assert val.value == Decimal("1.35")
    assert isinstance(val.value, Decimal)
