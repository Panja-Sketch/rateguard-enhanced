from decimal import (
    ROUND_CEILING,
    ROUND_FLOOR,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    Decimal,
)

from app.ipir.enums import RoundingMode

MODE_MAP = {
    RoundingMode.HALF_UP: ROUND_HALF_UP,
    RoundingMode.HALF_EVEN: ROUND_HALF_EVEN,
    RoundingMode.FLOOR: ROUND_FLOOR,
    RoundingMode.CEILING: ROUND_CEILING,
}


def round_decimal(value: Decimal, precision: int, mode: RoundingMode) -> Decimal:
    """Rounds a Decimal value to the specified precision and mode using exact Decimal arithmetic.

    Args:
        value: Input Decimal to round.
        precision: Number of decimal places (e.g. 2 for money, 4 for factors).
        mode: RoundingMode enum value.

    Returns:
        Quantized Decimal value.
    """
    if not isinstance(value, Decimal):
        value = Decimal(str(value))

    rounding = MODE_MAP.get(mode, ROUND_HALF_UP)
    exponent = Decimal("10") ** (-precision) if precision > 0 else Decimal("1")
    return value.quantize(exponent, rounding=rounding)
