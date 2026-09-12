import pytest
from pydantic import ValidationError

from app.ipir.enums import InputDataType
from app.ipir.inputs import PricingInput


def test_pricing_input_valid() -> None:
    inp = PricingInput(
        id="roof_age",
        name="Roof Age",
        data_type=InputDataType.INTEGER,
        minimum=0,
        maximum=100,
    )
    assert inp.id == "roof_age"
    assert inp.minimum == 0


def test_pricing_input_invalid_range() -> None:
    with pytest.raises(ValidationError, match="minimum .* cannot exceed maximum"):
        PricingInput(
            id="roof_age",
            name="Roof Age",
            data_type=InputDataType.INTEGER,
            minimum=100,
            maximum=10,
        )


def test_pricing_input_boolean_with_allowed_values() -> None:
    with pytest.raises(ValidationError, match="Boolean input .* should not define allowed_values"):
        PricingInput(
            id="has_pool",
            name="Has Swimming Pool",
            data_type=InputDataType.BOOLEAN,
            allowed_values=["yes", "no"],
        )
