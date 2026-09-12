from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import networkx as nx

from app.engines.oracle.condition_evaluator import evaluate_condition, resolve_value
from app.engines.oracle.errors import (
    EffectiveDateError,
    ExpressionEvaluationError,
    InputValidationError,
    OracleError,
)
from app.engines.oracle.expression_evaluator import evaluate_expression
from app.engines.oracle.models import OracleResult, PremiumTrace, RiskInput
from app.engines.oracle.rounding import round_decimal
from app.engines.oracle.table_lookup import lookup_table
from app.ipir.calculations import CalculationNode
from app.ipir.common import EffectivePeriod
from app.ipir.enums import (
    ConstraintType,
    InputDataType,
    ModifierType,
    RoundingMode,
    TransactionType,
)
from app.ipir.expressions import Expression
from app.ipir.package import IPIRPackage


def is_active(effective_period: EffectivePeriod | None, effective_date: date) -> bool:
    """Checks whether a component is active on the given calculation date."""
    if effective_period is None:
        return True
    if effective_period.start > effective_date:
        return False
    if effective_period.end is not None and effective_period.end < effective_date:
        return False
    return True


def validate_risk_inputs(
    package: IPIRPackage, risk: RiskInput, strict: bool = False
) -> dict[str, Any]:
    """Validates supplied risk inputs against the package input definitions."""
    validated: dict[str, Any] = {}
    supplied_keys = set(risk.values.keys())
    package_input_ids = {inp.id for inp in package.inputs}

    for inp in package.inputs:
        val = risk.get(inp.id)

        if val is None:
            if inp.id == "claims_free" and "claims_free_years" in risk.values:
                val = int(risk.values["claims_free_years"]) >= 3
            elif inp.id == "claims_free_years" and "claims_free" in risk.values:
                cf_bool = str(risk.values["claims_free"]).lower() in ("true", "1")
                val = 3 if cf_bool else 0
            elif inp.required:
                raise InputValidationError(f"Missing required rating input: '{inp.id}'")
            else:
                continue

        # Type conversion & validation
        dt = inp.data_type
        converted_val: Any = val

        try:
            if dt == InputDataType.INTEGER:
                converted_val = int(val)
            elif dt in (InputDataType.DECIMAL, InputDataType.MONEY):
                converted_val = Decimal(str(val))
            elif dt in (InputDataType.STRING, InputDataType.CATEGORY):
                converted_val = str(val)
            elif dt == InputDataType.BOOLEAN:
                if isinstance(val, bool):
                    converted_val = val
                elif str(val).lower() in ("true", "1"):
                    converted_val = True
                elif str(val).lower() in ("false", "0"):
                    converted_val = False
                else:
                    raise InputValidationError(
                        f"Input '{inp.id}' value '{val}' cannot be parsed as BOOLEAN"
                    )
            elif dt == InputDataType.DATE:
                if isinstance(val, date):
                    converted_val = val
                else:
                    converted_val = date.fromisoformat(str(val))
        except (ValueError, TypeError, InvalidOperation) as e:
            raise InputValidationError(
                f"Input '{inp.id}' value '{val}' is not valid for type {dt.value}: {e}"
            ) from e

        # Check allowed values for categories
        if inp.allowed_values is not None:
            if str(converted_val) not in inp.allowed_values:
                raise InputValidationError(
                    f"Input '{inp.id}' value '{converted_val}' is not in allowed values: "
                    f"{inp.allowed_values}"
                )

        # Check min/max for numeric types
        if dt in (InputDataType.INTEGER, InputDataType.DECIMAL, InputDataType.MONEY):
            num_dec = Decimal(str(converted_val))
            if inp.minimum is not None and num_dec < Decimal(str(inp.minimum)):
                raise InputValidationError(
                    f"Input '{inp.id}' value {converted_val} is below minimum {inp.minimum}"
                )
            if inp.maximum is not None and num_dec > Decimal(str(inp.maximum)):
                raise InputValidationError(
                    f"Input '{inp.id}' value {converted_val} exceeds maximum {inp.maximum}"
                )

        validated[inp.id] = converted_val

    if strict:
        extra_keys = supplied_keys - package_input_ids
        if extra_keys:
            raise InputValidationError(
                f"Unsupported extra inputs provided in strict mode: {extra_keys}"
            )

    return validated


def topological_sort_calculations(package: IPIRPackage) -> list[CalculationNode]:
    """Sorts package calculation nodes deterministically according to explicit dependencies."""
    calc_map = {node.id: node for node in package.calculations}
    graph = nx.DiGraph()

    for node_id in calc_map:
        graph.add_node(node_id)

    for node_id, node in calc_map.items():
        for dep in node.depends_on:
            if dep in calc_map:
                graph.add_edge(dep, node_id)

    try:
        sorted_ids = list(nx.topological_sort(graph))
        return [calc_map[nid] for nid in sorted_ids if nid in calc_map]
    except nx.NetworkXUnfeasible as e:
        raise ExpressionEvaluationError(
            f"Dependency cycle detected among calculation nodes: {e}"
        ) from e


def evaluate_package(
    package: IPIRPackage,
    risk: RiskInput,
    effective_date: date,
    transaction_type: TransactionType = TransactionType.NEW_BUSINESS,
    strict_inputs: bool = False,
) -> OracleResult:
    """Evaluates an IPIR package against risk inputs and produces a deterministic OracleResult."""
    # 1. Package Effective Date & Transaction Validation
    if not is_active(package.effective_period, effective_date):
        raise EffectiveDateError(
            f"Package '{package.id}' is not active on calculation date {effective_date}. "
            f"Package period: {package.effective_period.start} to {package.effective_period.end}"
        )

    if transaction_type not in package.transaction_types:
        raise OracleError(
            f"Transaction type '{transaction_type.value}' is not supported by package "
            f"'{package.id}'. Supported: {[t.value for t in package.transaction_types]}"
        )

    trace = PremiumTrace()
    context: dict[str, Any] = {}

    # 2. Validate & Store Risk Inputs
    validated_inputs = validate_risk_inputs(package, risk, strict=strict_inputs)
    for inp_id, inp_val in validated_inputs.items():
        context[inp_id] = inp_val
        trace.add_step(
            node_id=inp_id,
            node_type="INPUT",
            operation="SET_INPUT",
            result=inp_val,
            inputs={"raw_value": risk.get(inp_id)},
            description=f"Risk input '{inp_id}' validated",
        )

    # 3. Resolve Active Constants
    for const in package.constants:
        if is_active(const.effective_period, effective_date):
            context[const.id] = const.value
            trace.add_step(
                node_id=const.id,
                node_type="CONSTANT",
                operation="SET_CONSTANT",
                result=const.value,
                description=f"Constant '{const.name}' resolved",
                provenance=const.provenance,
            )

    # 4. Resolve Active Fees, Constraints, Modifiers IDs into context
    for fee in package.fees:
        if is_active(fee.effective_period, effective_date):
            context[fee.id] = fee.amount
        else:
            context[fee.id] = Decimal("0.00")

    for con in package.constraints:
        if is_active(con.effective_period, effective_date):
            context[con.id] = con.amount
        else:
            context[con.id] = Decimal("0.00")

    for mod in package.modifiers:
        if is_active(mod.effective_period, effective_date):
            eligible = True
            if mod.eligibility:
                eligible = evaluate_condition(mod.eligibility, context)
            if eligible:
                raw_val = resolve_value(mod.value, context)
                if mod.modifier_type == ModifierType.PERCENTAGE_DISCOUNT:
                    mod_val = Decimal("1.00") - Decimal(str(raw_val))
                elif mod.modifier_type == ModifierType.PERCENTAGE_SURCHARGE:
                    mod_val = Decimal("1.00") + Decimal(str(raw_val))
                else:
                    mod_val = Decimal(str(raw_val))
            else:
                mod_val = Decimal("1.00")
            context[mod.id] = mod_val
        else:
            context[mod.id] = Decimal("1.00")

    # 5. Resolve Active Rate Tables
    for table in package.tables:
        if is_active(table.effective_period, effective_date):
            dim_values = {dim.input_ref: context.get(dim.input_ref) for dim in table.dimensions}
            factor_val, row = lookup_table(table, dim_values)
            context[table.id] = factor_val
            trace.add_step(
                node_id=table.id,
                node_type="TABLE",
                operation="LOOKUP_TABLE",
                result=factor_val,
                inputs=dim_values,
                description=f"Rate table '{table.name}' looked up factor {factor_val}",
                provenance=table.provenance,
            )

    # 6. Resolve Active Rules
    for rule in package.rules:
        if is_active(rule.effective_period, effective_date):
            condition_holds = evaluate_condition(rule.condition, context)
            target = rule.when_true if condition_holds else rule.when_false
            rule_val = evaluate_expression(target, context)
            context[rule.id] = rule_val
            trace.add_step(
                node_id=rule.id,
                node_type="RULE",
                operation="EVALUATE_RULE",
                result=rule_val,
                inputs={"condition_holds": condition_holds},
                description=f"Pricing rule '{rule.name}' evaluated to {rule_val}",
                provenance=rule.provenance,
            )

    active_modifiers = [
        m for m in package.modifiers if is_active(m.effective_period, effective_date)
    ]
    active_modifiers.sort(key=lambda m: m.sequence if m.sequence is not None else 0)

    active_constraints = [
        c for c in package.constraints if is_active(c.effective_period, effective_date)
    ]
    active_constraints.sort(key=lambda c: c.sequence if c.sequence is not None else 0)

    active_fees = [f for f in package.fees if is_active(f.effective_period, effective_date)]
    active_fees.sort(key=lambda f: f.sequence if f.sequence is not None else 0)

    # 7. Execute Calculation Nodes in Topological Order
    sorted_nodes = topological_sort_calculations(package)
    processed_modifiers: set[str] = set()
    processed_constraints: set[str] = set()
    processed_fees: set[str] = set()

    for node in sorted_nodes:
        if not is_active(node.effective_period, effective_date):
            continue

        # Evaluate base calculation node expression
        base_val = evaluate_expression(node.expression, context)
        curr_val = Decimal(str(base_val))

        rr = node.rounding_rule
        if rr is None and node.rounding_rule_ref:
            rr = next((r for r in package.rounding_rules if r.id == node.rounding_rule_ref), None)

        if rr:
            curr_val = round_decimal(curr_val, rr.precision, rr.mode)

        # Apply active modifiers declared in this calculation node's depends_on list
        mod_matches = [
            m
            for m in active_modifiers
            if m.id in node.depends_on and m.id not in processed_modifiers
        ]
        for mod in mod_matches:
            processed_modifiers.add(mod.id)
            if mod.eligibility is not None and not evaluate_condition(mod.eligibility, context):
                trace.add_step(
                    node_id=mod.id,
                    node_type="MODIFIER",
                    operation="SKIPPED_INELIGIBLE",
                    result=Decimal("0.00"),
                    inputs={"eligibility": False},
                    description=f"Modifier '{mod.name}' skipped (ineligible)",
                    provenance=mod.provenance,
                )
                continue

            mod_val = Decimal(str(resolve_value(mod.value, context)))
            adj_amount = Decimal("0")

            if mod.modifier_type == ModifierType.PERCENTAGE_DISCOUNT:
                adj_amount = round_decimal(curr_val * mod_val, 2, RoundingMode.HALF_UP)
                curr_val = curr_val - adj_amount
            elif mod.modifier_type == ModifierType.PERCENTAGE_SURCHARGE:
                adj_amount = round_decimal(curr_val * mod_val, 2, RoundingMode.HALF_UP)
                curr_val = curr_val + adj_amount
            elif mod.modifier_type == ModifierType.FLAT_DISCOUNT:
                adj_amount = mod_val
                curr_val = curr_val - adj_amount
            elif mod.modifier_type == ModifierType.FLAT_SURCHARGE:
                adj_amount = mod_val
                curr_val = curr_val + adj_amount

            context[mod.id] = adj_amount
            trace.add_step(
                node_id=mod.id,
                node_type="MODIFIER",
                operation=mod.modifier_type.value,
                result=adj_amount,
                inputs={"target": mod.applies_to, "modifier_value": mod_val},
                description=f"Applied modifier '{mod.name}': adjustment={adj_amount}",
                provenance=mod.provenance,
                metadata={"adjustment_amount": adj_amount, "post_modifier_total": curr_val},
            )

        # Apply active constraints declared in this node's depends_on list
        con_matches = [
            c
            for c in active_constraints
            if c.id in node.depends_on and c.id not in processed_constraints
        ]
        for con in con_matches:
            processed_constraints.add(con.id)
            prev_val = curr_val
            if con.constraint_type == ConstraintType.MINIMUM:
                curr_val = max(curr_val, con.amount)
            elif con.constraint_type == ConstraintType.MAXIMUM:
                curr_val = min(curr_val, con.amount)

            trace.add_step(
                node_id=con.id,
                node_type="CONSTRAINT",
                operation=con.constraint_type.value,
                result=curr_val,
                inputs={
                    "target": con.applies_to,
                    "previous_value": prev_val,
                    "constraint_amount": con.amount,
                },
                description=f"Enforced constraint '{con.name}': {prev_val} -> {curr_val}",
                provenance=con.provenance,
            )

        # Apply active fees declared in this node's depends_on list
        fee_matches = [
            f for f in active_fees if f.id in node.depends_on and f.id not in processed_fees
        ]
        for fee in fee_matches:
            processed_fees.add(fee.id)
            if isinstance(node.expression, Expression) and fee.id in [
                op.ref for op in node.expression.operands if hasattr(op, "ref")
            ]:
                pass
            else:
                prev_val = curr_val
                curr_val = curr_val + fee.amount
                trace.add_step(
                    node_id=fee.id,
                    node_type="FEE",
                    operation="ADD_FEE",
                    result=curr_val,
                    inputs={
                        "target": fee.applies_to,
                        "previous_value": prev_val,
                        "fee_amount": fee.amount,
                    },
                    description=f"Applied fee '{fee.name}': +{fee.amount} -> {curr_val}",
                    provenance=fee.provenance,
                )

        context[node.id] = curr_val
        trace.add_step(
            node_id=node.id,
            node_type="CALCULATION",
            operation="EVALUATE_CALCULATION",
            result=curr_val,
            inputs={dep: context.get(dep) for dep in node.depends_on},
            description=node.description or f"Calculation node '{node.name}' evaluated",
            provenance=node.provenance,
        )

    # 8. Resolve Outputs
    if not package.outputs:
        raise OracleError(f"IPIR Package '{package.id}' defines no outputs")

    out_def = package.outputs[0]
    final_premium = Decimal(str(context[out_def.source_ref]))
    trace.add_step(
        node_id=out_def.id,
        node_type="OUTPUT",
        operation="FINAL_OUTPUT",
        result=final_premium,
        inputs={"source_ref": out_def.source_ref},
        description=f"Final output '{out_def.name}' resolved to {final_premium}",
    )

    return OracleResult(
        package_id=package.id,
        package_version=package.version,
        effective_date=effective_date,
        transaction_type=transaction_type,
        final_output_id=out_def.id,
        final_premium=final_premium,
        currency=out_def.currency or "USD",
        resolved_values=context,
        trace=trace,
    )
