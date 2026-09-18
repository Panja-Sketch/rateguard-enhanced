"""The v0.2 -> v0.1 compatibility/lowering boundary (docs/implementation/
DECISIONS.md, D2). Converts an `IPIRPackageV2` into a plain `app.ipir.package.
IPIRPackage` so the *existing, unmodified* deterministic oracle evaluator
(`app.engines.oracle.evaluator.evaluate_package`) can price it — this is what
lets IPIR v0.2 become authoritative for new flows without maintaining a
second pricing-engine implementation.

Every construct that has no faithful v0.1 runtime equivalent fails closed
with `LoweringNotSupportedError` rather than being silently approximated.
See each function's docstring for the specific mapping chosen and why.
"""

from datetime import date
from decimal import Decimal
from typing import Any

from app.engines.oracle.evaluator import evaluate_package
from app.engines.oracle.models import RiskInput
from app.ipir.common import LiteralValue, NodeReference
from app.ipir.constraints import RoundingRule
from app.ipir.enums import TransactionType
from app.ipir.package import IPIRPackage
from app.ipir.product import InsuranceProduct
from app.ipir.rules import ComparisonCondition, LogicalCondition, PricingRule
from app.ipir.v0_2.control_cases import ControlCase, ControlCaseResult
from app.ipir.v0_2.errors import LoweringNotSupportedError
from app.ipir.v0_2.expressions import (
    BinaryExpression,
    ComparisonConditionV2,
    ConditionalExpression,
    ExpressionV2,
    LiteralExpression,
    LogicalConditionV2,
    NaryExpression,
    ReferenceExpression,
    RoundExpression,
    TableLookupExpression,
    UnaryExpression,
)
from app.ipir.v0_2.package import IPIRPackageV2


class _LoweringContext:
    """Accumulates side effects of lowering a package's calculation graph —
    specifically, the v0.1 PricingRule objects synthesized from v0.2
    ConditionalExpression nodes (see `_lower_expression`). A plain mutable
    accumulator rather than a return-tuple threaded through every recursive
    call, to keep `_lower_expression`'s signature identical to v0.1's own
    expression shape."""

    def __init__(self) -> None:
        self.synthesized_rules: list[PricingRule] = []
        self._rule_counter = 0

    def next_rule_id(self, calc_id: str) -> str:
        self._rule_counter += 1
        return f"_lowered_rule_{calc_id}_{self._rule_counter}"


def _lower_condition(
    condition: "ComparisonConditionV2 | LogicalConditionV2", ctx: _LoweringContext
) -> "ComparisonCondition | LogicalCondition":
    if isinstance(condition, ComparisonConditionV2):
        return ComparisonCondition(
            left=_lower_expression(condition.left, ctx),
            operator=condition.operator,
            right=_lower_expression(condition.right, ctx),
        )
    return LogicalCondition(
        operator=condition.operator,
        conditions=[_lower_condition(c, ctx) for c in condition.conditions],
    )


def _lower_expression(
    expr: ExpressionV2, ctx: _LoweringContext, *, calc_id: str = "expr"
) -> "Any":
    """Recursively lowers one v0.2 expression node into the v0.1 shape
    (`Expression | NodeReference | LiteralValue`) that
    `app.engines.oracle.expression_evaluator.evaluate_expression` already
    knows how to run unchanged."""
    from app.ipir.expressions import Expression as ExpressionV1

    if isinstance(expr, LiteralExpression):
        return LiteralValue(value=expr.value)

    if isinstance(expr, ReferenceExpression):
        return NodeReference(ref=expr.ref)

    if isinstance(expr, TableLookupExpression):
        # v0.1 pre-resolves every active table's factor into the evaluation
        # context once per run (see evaluator.py step 5), keyed by table id —
        # exactly what a NodeReference to the table's own id already exposes.
        return NodeReference(ref=expr.table_id)

    if isinstance(expr, UnaryExpression):
        if expr.operator == "NEGATE":
            return ExpressionV1(
                operator="SUBTRACT",
                operands=[LiteralValue(value=Decimal("0")), _lower_expression(expr.operand, ctx)],
            )
        raise LoweringNotSupportedError(f"Unsupported UnaryExpression operator '{expr.operator}'.")

    if isinstance(expr, BinaryExpression):
        return ExpressionV1(
            operator=expr.operator,
            operands=[
                _lower_expression(expr.left, ctx),
                _lower_expression(expr.right, ctx),
            ],
        )

    if isinstance(expr, NaryExpression):
        return ExpressionV1(
            operator=expr.operator,
            operands=[_lower_expression(o, ctx) for o in expr.operands],
        )

    if isinstance(expr, RoundExpression):
        # A *nested* ROUND (i.e. this call did not come from the top-level
        # dispatch in `_lower_calculation_node`) has no faithful v0.1
        # equivalent: v0.1 only applies rounding once, after a whole
        # calculation node's expression has been evaluated
        # (`CalculationNode.rounding_rule`), not at an arbitrary point inside
        # an expression tree. Failing closed here is deliberate — see
        # docs/implementation/DECISIONS.md (D2).
        raise LoweringNotSupportedError(
            "A RoundExpression nested inside another expression (not at the top level of "
            "a calculation node) is not supported by the v0.2->v0.1 compatibility lowering."
        )

    if isinstance(expr, ConditionalExpression):
        # v0.1 has no inline conditional expression; it models IF/THEN/ELSE
        # only as a top-level PricingRule, evaluated once into the shared
        # context before calculations run. Reusing that existing, tested
        # machinery means the evaluator needs zero changes: synthesize a
        # rule and substitute a reference to it in its place.
        rule_id = ctx.next_rule_id(calc_id)
        ctx.synthesized_rules.append(
            PricingRule(
                id=rule_id,
                name=f"Lowered conditional for '{calc_id}'",
                condition=_lower_condition(expr.condition, ctx),
                when_true=_lower_expression(expr.when_true, ctx),
                when_false=_lower_expression(expr.when_false, ctx),
            )
        )
        return NodeReference(ref=rule_id)

    raise LoweringNotSupportedError(f"Unsupported expression node type: {type(expr).__name__}")


def _lower_calculation_node(node, ctx: _LoweringContext):
    from app.ipir.calculations import CalculationNode as CalculationNodeV1

    rounding_rule: RoundingRule | None = None
    expression = node.expression
    if isinstance(expression, RoundExpression):
        rounding_rule = RoundingRule(
            id=f"_lowered_rounding_{node.id}",
            precision=expression.scale,
            mode=expression.rounding_mode,
        )
        expression = expression.operand

    return CalculationNodeV1(
        id=node.id,
        name=node.name,
        expression=_lower_expression(expression, ctx, calc_id=node.id),
        depends_on=node.depends_on,
        rounding_rule=rounding_rule,
        effective_period=node.effective_period,
        description=node.description,
    )


def lower_to_v0_1(package: IPIRPackageV2) -> IPIRPackage:
    """Converts a validated `IPIRPackageV2` into a plain v0.1 `IPIRPackage`
    that `app.engines.oracle.evaluator.evaluate_package` can run without any
    change to that evaluator. Raises `LoweringNotSupportedError` for any
    construct without a faithful v0.1 equivalent (see module docstring)."""
    ctx = _LoweringContext()
    calculations = [_lower_calculation_node(node, ctx) for node in package.calculations]

    return IPIRPackage(
        id=package.package_id,
        name=package.package_id,
        version=package.package_version,
        product=InsuranceProduct(
            id=package.product.product_id,
            name=package.product.product_id,
            line=package.product.line,
            jurisdiction=package.product.jurisdiction,
        ),
        effective_period=package.effective_period,
        transaction_types=package.transaction_types,
        inputs=list(package.inputs),
        constants=list(package.constants),
        tables=list(package.tables),
        rules=ctx.synthesized_rules,
        calculations=calculations,
        outputs=[
            {
                "id": out.id,
                "name": out.name,
                "source_ref": out.source_ref,
                "currency": out.currency,
            }
            for out in package.outputs
        ],
    )


def run_control_cases(package: IPIRPackageV2) -> list[ControlCaseResult]:
    """Lowers `package` once and runs every embedded `ControlCase` against
    the existing oracle, comparing the resulting premium to the case's
    expected output within its declared Decimal tolerance (locked doc
    section 5.3, steps 10-11). This is the mechanism by which a v0.2-authored
    package proves itself against its own golden examples without a second
    pricing implementation."""
    lowered = lower_to_v0_1(package)
    results: list[ControlCaseResult] = []

    for case in package.control_cases:
        for output_id, expected_str in case.expected_outputs.items():
            output_def = next((o for o in package.outputs if o.id == output_id), None)
            if output_def is None:
                results.append(
                    ControlCaseResult(
                        case_id=case.case_id,
                        passed=False,
                        output_id=output_id,
                        expected=expected_str,
                        actual="",
                        difference="",
                        detail=f"Output '{output_id}' is not declared on this package.",
                    )
                )
                continue

            try:
                oracle_result = evaluate_package(
                    package=lowered,
                    risk=RiskInput(values=case.inputs),
                    effective_date=_case_effective_date(package, case),
                    transaction_type=_case_transaction_type(package, case),
                )
                actual = oracle_result.final_premium
                expected = Decimal(expected_str)
                difference = abs(actual - expected)
                results.append(
                    ControlCaseResult(
                        case_id=case.case_id,
                        passed=difference <= case.tolerance,
                        output_id=output_id,
                        expected=expected_str,
                        actual=str(actual),
                        difference=str(difference),
                    )
                )
            except Exception as exc:  # noqa: BLE001 - reported as a failed control case, not raised
                results.append(
                    ControlCaseResult(
                        case_id=case.case_id,
                        passed=False,
                        output_id=output_id,
                        expected=expected_str,
                        actual="",
                        difference="",
                        detail=f"{type(exc).__name__}: {exc}",
                    )
                )

    return results


def _case_effective_date(package: IPIRPackageV2, case: ControlCase) -> date:
    raw = case.inputs.get("effective_date")
    if isinstance(raw, str):
        return date.fromisoformat(raw)
    return package.effective_period.start


def _case_transaction_type(package: IPIRPackageV2, case: ControlCase) -> TransactionType:
    raw = case.inputs.get("transaction_type")
    if isinstance(raw, str):
        return TransactionType(raw)
    return package.transaction_types[0]
