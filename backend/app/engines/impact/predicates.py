from datetime import date
from decimal import Decimal

from app.engines.diff.enums import DifferenceType
from app.engines.diff.models import SemanticDifference
from app.engines.impact.models import ImpactPredicate, PredicateClause
from app.ipir.common import LiteralValue, NodeReference
from app.ipir.enums import ComparisonOperator, LogicalOperator
from app.ipir.package import IPIRPackage
from app.ipir.rules import ComparisonCondition
from app.ipir.tables import RangeMatch, TableLookupType

# A predicate with no clauses and no temporal window matches every policy
# (see app.engines.portfolio.predicate_evaluator.matches_predicate) -- this
# is the correct, honest representation for a change with no risk-based
# eligibility of its own (a flat fee, a premium constraint, or any diff type
# this module doesn't yet know how to translate into specific clauses).
# Silently returning None here instead would mean the portfolio blast-radius
# scan and boundary-test generator never learn the change exists at all --
# an undercounted "financially affected" number is a false PASS in miniature.
def _global_predicate(diff: SemanticDifference) -> ImpactPredicate:
    return ImpactPredicate(
        id=f"pred_{diff.id}",
        clauses=[],
        logical_operator=LogicalOperator.AND,
        description=(
            f"'{diff.node_id}' change with no risk-based eligibility of its own -- "
            "applies to every policy that reaches the affected calculation node."
        ),
    )


def _predicate_from_eligibility(
    diff: SemanticDifference, eligibility: ComparisonCondition
) -> ImpactPredicate | None:
    """Translates a modifier's simple `left ref/right literal` (or reversed)
    eligibility condition into a single-clause predicate. Anything more
    complex (a LogicalCondition, both sides literals/expressions, or a
    non-EQ/NE comparison against a non-input reference) falls back to the
    caller's global predicate instead of guessing -- a predicate that
    matches too broadly is safe (it only costs extra repricing work); one
    that matches too narrowly silently hides real exposure."""
    if isinstance(eligibility.left, NodeReference) and isinstance(eligibility.right, LiteralValue):
        field, literal = eligibility.left.ref, eligibility.right.value
    elif isinstance(eligibility.right, NodeReference) and isinstance(eligibility.left, LiteralValue):
        field, literal = eligibility.right.ref, eligibility.left.value
    else:
        return None

    return ImpactPredicate(
        id=f"pred_{diff.id}",
        clauses=[PredicateClause(field=field, operator=eligibility.operator, value=literal)],
        logical_operator=LogicalOperator.AND,
        description=f"Risk attribute condition matching '{diff.node_id}' eligibility ({field} {eligibility.operator.value} {literal}).",
    )


def derive_predicate_from_difference(
    diff: SemanticDifference, package: IPIRPackage
) -> ImpactPredicate:
    """Derives a structured ImpactPredicate describing policy risk conditions exercising a
    diff. Always returns a predicate -- one with no clauses matches every policy (see
    `_global_predicate`), which is the honest answer for a diff type this module can't
    translate into narrower risk conditions, not an excuse to drop it from scope silently."""
    # 1. Effective Date Drift
    if diff.difference_type == DifferenceType.EFFECTIVE_DATE_CHANGE:
        start_a = date.fromisoformat(str(diff.left_value)) if diff.left_value else None
        start_b = date.fromisoformat(str(diff.right_value)) if diff.right_value else None

        min_start = min(filter(None, [start_a, start_b])) if (start_a or start_b) else None
        max_start = max(filter(None, [start_a, start_b])) if (start_a or start_b) else None

        return ImpactPredicate(
            id=f"pred_{diff.id}",
            clauses=[],
            logical_operator=LogicalOperator.AND,
            temporal_start=min_start,
            temporal_end=max_start,
            description=(
                f"Temporal discrepancy window for '{diff.node_id}' between "
                f"{min_start} and {max_start}"
            ),
        )

    # 2. Table Row Differences (1D Exact, 1D Range, 2D Exact)
    if diff.difference_type in (
        DifferenceType.TABLE_ROW_CHANGE,
        DifferenceType.TABLE_ROW_MISSING,
        DifferenceType.TABLE_ROW_EXTRA,
    ):
        table = next((t for t in package.tables if t.id == diff.node_id), None)
        if not table:
            return _global_predicate(diff)

        dim_key = diff.metadata.get("dimension_key", "")
        key_parts = dim_key.split("|")
        clauses: list[PredicateClause] = []

        for dim, key_part in zip(table.dimensions, key_parts, strict=False):
            field_name = dim.input_ref

            if dim.lookup_type == TableLookupType.EXACT:
                clauses.append(
                    PredicateClause(
                        field=field_name,
                        operator=ComparisonOperator.EQ,
                        value=key_part,
                    )
                )
            elif dim.lookup_type == TableLookupType.RANGE and ".." in key_part:
                min_str, max_str = key_part.split("..")
                if min_str != "-inf":
                    clauses.append(
                        PredicateClause(
                            field=field_name,
                            operator=ComparisonOperator.GTE,
                            value=Decimal(min_str),
                        )
                    )
                if max_str != "+inf":
                    clauses.append(
                        PredicateClause(
                            field=field_name,
                            operator=ComparisonOperator.LTE,
                            value=Decimal(max_str),
                        )
                    )

        return ImpactPredicate(
            id=f"pred_{diff.id}",
            clauses=clauses,
            logical_operator=LogicalOperator.AND,
            description=f"Risk attributes matching table lookup '{table.id}' key [{dim_key}]",
        )

    # 3. Modifier Value/Sequence Changes -- a discount/surcharge only applies
    # to the policies its own `eligibility` condition selects (e.g.
    # `multi_policy == true`); a modifier with no eligibility applies to
    # every policy, same as a fee or constraint below.
    if diff.difference_type == DifferenceType.MODIFIER_CHANGE:
        modifier = next((m for m in package.modifiers if m.id == diff.node_id), None)
        if modifier is not None and isinstance(modifier.eligibility, ComparisonCondition):
            pred = _predicate_from_eligibility(diff, modifier.eligibility)
            if pred is not None:
                return pred
        return _global_predicate(diff)

    # 4. Fee and Constraint Changes -- neither carries an eligibility
    # condition in the IPIR schema (see app.ipir.constraints), so both are
    # unconditionally global: every policy that reaches the affected
    # calculation node is exposed.
    if diff.difference_type in (DifferenceType.FEE_CHANGE, DifferenceType.CONSTRAINT_CHANGE):
        return _global_predicate(diff)

    # 5. Anything else this module doesn't have a specific translation for
    # yet (VALUE_CHANGE on a constant, RULE_CHANGE, ORDER_CHANGE, ROUNDING_
    # CHANGE, OUTPUT_CHANGE, ...) still deserves a predicate rather than
    # silently vanishing from blast-radius/boundary-test scope -- see
    # `_global_predicate`'s docstring.
    return _global_predicate(diff)


def derive_predicates_from_package(package: IPIRPackage) -> list[ImpactPredicate]:
    """Derives boundary risk predicates directly from a package's own rate table
    rows, with no semantic diff to key off of.

    Used for Runtime Verification mode, which has no Source B / diff — pricing
    correctness there is judged solely by probing a black-box rating API, so
    the boundary conditions worth probing must come from Source A's own rate
    table range dimensions instead of a diff's changed row.
    """
    predicates: list[ImpactPredicate] = []
    counter = 0

    for table in package.tables:
        range_dim_indices = [
            i for i, dim in enumerate(table.dimensions) if dim.lookup_type == TableLookupType.RANGE
        ]
        if not range_dim_indices:
            continue

        for row in table.rows:
            clauses: list[PredicateClause] = []
            for dim, match in zip(table.dimensions, row.matches, strict=False):
                if isinstance(match, RangeMatch):
                    if match.minimum is not None:
                        clauses.append(
                            PredicateClause(
                                field=dim.input_ref, operator=ComparisonOperator.GTE, value=match.minimum,
                            )
                        )
                    if match.maximum is not None:
                        clauses.append(
                            PredicateClause(
                                field=dim.input_ref, operator=ComparisonOperator.LTE, value=match.maximum,
                            )
                        )
                else:
                    clauses.append(
                        PredicateClause(field=dim.input_ref, operator=ComparisonOperator.EQ, value=match.value)
                    )

            if not any(c.operator in (ComparisonOperator.GTE, ComparisonOperator.LTE) for c in clauses):
                continue

            counter += 1
            predicates.append(
                ImpactPredicate(
                    id=f"pred_self_{table.id}_{counter}",
                    clauses=clauses,
                    logical_operator=LogicalOperator.AND,
                    description=f"Risk attributes matching rate table '{table.id}' row boundary.",
                )
            )

    return predicates
