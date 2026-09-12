from decimal import Decimal


def generate_range_boundary_values(
    minimum: int | Decimal | str | None, maximum: int | Decimal | str | None
) -> dict[str, int]:
    """Generates boundary values (lower bound, upper bound, interior, just below, just above)."""
    min_num = int(str(minimum)) if minimum is not None else None
    max_num = int(str(maximum)) if maximum is not None else None

    boundaries: dict[str, int] = {}

    if min_num is not None:
        boundaries["just_below"] = min_num - 1
        boundaries["lower_bound"] = min_num

    if min_num is not None and max_num is not None:
        boundaries["interior"] = (min_num + max_num) // 2

    if max_num is not None:
        boundaries["upper_bound"] = max_num
        boundaries["just_above"] = max_num + 1

    return boundaries
