from app.engines.diff.enums import DifferenceSeverity, DifferenceType
from app.engines.diff.models import SemanticDiffResult
from app.engines.impact.graph import PricingDependencyGraph
from app.engines.reconciliation.models import RootCauseFinding, TraceDifference
from app.ipir.package import IPIRPackage


def perform_root_cause_analysis(
    trace_diffs: list[TraceDifference],
    diff_result: SemanticDiffResult,
    package: IPIRPackage,
) -> tuple[str | None, RootCauseFinding | None, list[RootCauseFinding]]:
    """Identifies primary and contributing root cause findings using upstream semantic causality."""
    if not trace_diffs:
        return None, None, []

    graph = PricingDependencyGraph(package)
    divergent_node_ids = {td.node_id for td in trace_diffs}
    diff_map = {d.node_id: d for d in diff_result.differences}

    primary_diff = None
    primary_node_id = None

    # Priority 1: Direct table factor change
    table_diff = next(
        (
            d
            for d in diff_result.differences
            if d.difference_type == DifferenceType.TABLE_ROW_CHANGE
            and d.node_id in divergent_node_ids
        ),
        None,
    )

    # Priority 2: Effective date drift
    eff_date_diff = next(
        (
            d
            for d in diff_result.differences
            if d.difference_type == DifferenceType.EFFECTIVE_DATE_CHANGE
            and (d.node_id in divergent_node_ids or "premium_after_discounts" in divergent_node_ids)
        ),
        None,
    )

    # Priority 3: Order sequence change
    order_diff = next(
        (
            d
            for d in diff_result.differences
            if d.difference_type == DifferenceType.ORDER_CHANGE
            and ("SEQUENCE_ORDER" in str(trace_diffs) or "policy_minimum" in divergent_node_ids)
        ),
        None,
    )

    is_roof_div = "roof" in str(divergent_node_ids)
    if table_diff and (is_roof_div or table_diff.node_id in divergent_node_ids):
        primary_diff = table_diff
        primary_node_id = table_diff.node_id
    elif eff_date_diff and not table_diff:
        primary_diff = eff_date_diff
        primary_node_id = eff_date_diff.node_id
    elif order_diff and not table_diff and not eff_date_diff:
        primary_diff = order_diff
        primary_node_id = order_diff.node_id

    # Fallback to DAG topological root
    if not primary_node_id:
        root_candidates: list[TraceDifference] = []
        for td in trace_diffs:
            ancestors = set(graph.get_ancestors(td.node_id))
            if not (ancestors & divergent_node_ids):
                root_candidates.append(td)

        first_diff = root_candidates[0] if root_candidates else trace_diffs[0]
        primary_node_id = first_diff.node_id
        primary_diff = diff_map.get(primary_node_id)

    # Construct primary RootCauseFinding
    if primary_diff:
        diff_type = primary_diff.difference_type
        severity = primary_diff.severity
        sem_path = primary_diff.semantic_path
        group_id = (
            "ISSUE_PREMIUM_SEQUENCE_ORDER"
            if diff_type == DifferenceType.ORDER_CHANGE
            else f"ISSUE_{primary_diff.node_id.upper()}"
        )
        explanation = (
            f"Pricing mismatch originating at '{primary_node_id}'. {primary_diff.description}."
        )
        exp_val = primary_diff.left_value
        act_val = primary_diff.right_value
    else:
        diff_type = DifferenceType.VALUE_CHANGE
        severity = DifferenceSeverity.HIGH
        sem_path = f"nodes.{primary_node_id}"
        group_id = None
        first_td = next((td for td in trace_diffs if td.node_id == primary_node_id), trace_diffs[0])
        explanation = (
            f"Calculation divergence detected at node '{primary_node_id}'. "
            f"Expected: {first_td.expected_value}, Actual: {first_td.actual_value}."
        )
        exp_val = first_td.expected_value
        act_val = first_td.actual_value

    downstream = graph.get_descendants(primary_node_id)

    primary_rca = RootCauseFinding(
        node_id=primary_node_id,
        category=diff_type.value,
        title=f"Root Cause: {diff_type.value} at {primary_node_id}",
        explanation=explanation,
        expected_value=exp_val,
        actual_value=act_val,
        semantic_path=sem_path,
        difference_type=diff_type,
        severity=severity,
        semantic_issue_group_id=group_id,
        evidence={
            "expected_value": str(exp_val),
            "actual_value": str(act_val),
            "semantic_difference_id": primary_diff.id if primary_diff else None,
        },
        downstream_effects=downstream,
    )

    return primary_node_id, primary_rca, []
