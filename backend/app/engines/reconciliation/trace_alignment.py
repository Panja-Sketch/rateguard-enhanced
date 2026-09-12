from decimal import Decimal, InvalidOperation
from typing import Any

from app.engines.oracle.models import PremiumTrace
from app.engines.reconciliation.models import TraceDifference
from app.ipir.package import IPIRPackage


def _to_decimal_safe(val: Any) -> Decimal | None:
    """Helper to convert value to Decimal if numeric."""
    if isinstance(val, (int, float, Decimal)) and not isinstance(val, bool):
        try:
            return Decimal(str(val))
        except (ValueError, TypeError, InvalidOperation):
            return None
    return None


def align_and_compare_traces(
    expected_trace: PremiumTrace, actual_trace: PremiumTrace, package: IPIRPackage
) -> list[TraceDifference]:
    """Aligns expected and actual execution traces by node_id and finds divergences."""
    differences: list[TraceDifference] = []
    actual_step_map = {step.node_id: step for step in actual_trace.steps}

    for seq_idx, exp_step in enumerate(expected_trace.steps, start=1):
        act_step = actual_step_map.get(exp_step.node_id)
        if not act_step:
            continue

        exp_val = exp_step.result
        act_val = act_step.result

        # Numerical or exact comparison
        exp_dec = _to_decimal_safe(exp_val)
        act_dec = _to_decimal_safe(act_val)

        is_different = False
        abs_diff: Decimal | None = None
        pct_diff: Decimal | None = None

        if exp_dec is not None and act_dec is not None:
            if exp_dec != act_dec:
                is_different = True
                abs_diff = abs(exp_dec - act_dec)
                if exp_dec != Decimal("0"):
                    pct_diff = round((abs_diff / exp_dec) * Decimal("100"), 4)
        else:
            if str(exp_val) != str(act_val):
                is_different = True

        if is_different:
            differences.append(
                TraceDifference(
                    node_id=exp_step.node_id,
                    node_type=exp_step.node_type,
                    expected_value=exp_val,
                    actual_value=act_val,
                    absolute_difference=abs_diff,
                    percentage_difference=pct_diff,
                    sequence_position=seq_idx,
                )
            )

    return differences
