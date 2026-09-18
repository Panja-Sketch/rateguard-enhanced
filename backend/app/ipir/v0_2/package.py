"""IPIR v0.2 root package envelope and semantic validation (locked doc
section 6). Reuses v0.1's structurally-sound leaf models (`PricingInput`,
`PricingConstant`, `RateTable`) directly rather than forking them solely to
change an ID-pattern regex — see docs/implementation/DECISIONS.md (D2) for
the rationale — and enforces the stricter v0.2 ID pattern as a single
aggregate validation pass here instead.
"""

import re
from collections.abc import Iterator

import networkx as nx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.ipir.common import EffectivePeriod
from app.ipir.enums import TransactionType
from app.ipir.inputs import PricingInput
from app.ipir.package import PricingConstant
from app.ipir.tables import RateTable, validate_range_coverage
from app.ipir.v0_2.attestation import Attestation
from app.ipir.v0_2.calculations import CalculationNodeV2
from app.ipir.v0_2.common import validate_identifier_string_v2
from app.ipir.v0_2.control_cases import ControlCase
from app.ipir.v0_2.envelope import ProductRefV2, SourceMetadata
from app.ipir.v0_2.expressions import (
    BinaryExpression,
    ComparisonConditionV2,
    ConditionalExpression,
    ExpressionV2,
    LogicalConditionV2,
    NaryExpression,
    ReferenceExpression,
    RoundExpression,
    TableLookupExpression,
    UnaryExpression,
)
from app.ipir.v0_2.outputs import PricingOutputV2

SCHEMA_VERSION_PATTERN = re.compile(r"^0\.2\.\d+$")


def _iter_expression_refs(expr: ExpressionV2) -> Iterator[tuple[str, str]]:
    """Walks an expression tree, yielding ("node", id) for every REFERENCE
    node and ("table", id) for every TABLE_LOOKUP node found anywhere in it
    (not just at the top level) — this is what makes unresolved-reference
    detection catch references buried inside nested arithmetic, not only
    top-level ones."""
    if isinstance(expr, ReferenceExpression):
        yield ("node", expr.ref)
    elif isinstance(expr, TableLookupExpression):
        yield ("table", expr.table_id)
    elif isinstance(expr, UnaryExpression):
        yield from _iter_expression_refs(expr.operand)
    elif isinstance(expr, BinaryExpression):
        yield from _iter_expression_refs(expr.left)
        yield from _iter_expression_refs(expr.right)
    elif isinstance(expr, NaryExpression):
        for operand in expr.operands:
            yield from _iter_expression_refs(operand)
    elif isinstance(expr, RoundExpression):
        yield from _iter_expression_refs(expr.operand)
    elif isinstance(expr, ConditionalExpression):
        yield from _iter_condition_refs(expr.condition)
        yield from _iter_expression_refs(expr.when_true)
        yield from _iter_expression_refs(expr.when_false)
    # LiteralExpression carries no references.


def _iter_condition_refs(
    condition: "ComparisonConditionV2 | LogicalConditionV2",
) -> Iterator[tuple[str, str]]:
    if isinstance(condition, ComparisonConditionV2):
        yield from _iter_expression_refs(condition.left)
        yield from _iter_expression_refs(condition.right)
    elif isinstance(condition, LogicalConditionV2):
        for child in condition.conditions:
            yield from _iter_condition_refs(child)


class IPIRPackageV2(BaseModel):
    """Root IPIR v0.2 package (locked doc section 6.1). Unknown top-level
    fields are rejected (`extra="forbid"`), matching v0.1's IPIRPackage
    philosophy of surfacing a typo'd field as a validation error rather than
    silently discarding real pricing content."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "0.2.0"
    package_id: str
    package_version: str = "1.0.0"
    source: SourceMetadata
    product: ProductRefV2
    effective_period: EffectivePeriod
    transaction_types: list[TransactionType] = Field(
        default_factory=lambda: [TransactionType.NEW_BUSINESS, TransactionType.RENEWAL]
    )
    inputs: list[PricingInput] = Field(default_factory=list)
    constants: list[PricingConstant] = Field(default_factory=list)
    tables: list[RateTable] = Field(default_factory=list)
    calculations: list[CalculationNodeV2] = Field(default_factory=list)
    outputs: list[PricingOutputV2] = Field(default_factory=list)
    control_cases: list[ControlCase] = Field(default_factory=list)
    attestation: Attestation | None = None

    @model_validator(mode="after")
    def validate_package(self) -> "IPIRPackageV2":
        if not SCHEMA_VERSION_PATTERN.match(self.schema_version):
            raise ValueError(f"schema_version must match '0.2.x', got '{self.schema_version}'.")
        self.package_id = validate_identifier_string_v2(self.package_id)

        # 1. Strict v0.2 ID pattern + duplicate-ID validation across the
        # package namespace. Leaf types are reused from v0.1 (which enforce
        # only the looser v0.1 pattern on themselves), so the stricter v0.2
        # pattern is enforced here in aggregate instead of by forking every
        # leaf model.
        seen: dict[str, str] = {}
        collections: list[tuple[str, list]] = [
            ("input", self.inputs),
            ("constant", self.constants),
            ("table", self.tables),
            ("calculation", self.calculations),
            ("output", self.outputs),
        ]
        for entity_type, items in collections:
            for item in items:
                validate_identifier_string_v2(item.id)
                if item.id in seen:
                    raise ValueError(
                        f"Duplicate node ID '{item.id}' found in package. "
                        f"First seen in {seen[item.id]}, duplicated in {entity_type}."
                    )
                seen[item.id] = entity_type
        node_ids = set(seen.keys())

        case_ids: set[str] = set()
        for case in self.control_cases:
            if case.case_id in case_ids:
                raise ValueError(f"Duplicate ControlCase ID '{case.case_id}' in package.")
            case_ids.add(case.case_id)

        # 2. Unresolved-reference detection: every REFERENCE/TABLE_LOOKUP
        # found anywhere inside a calculation's expression tree (not just
        # top-level operands) must resolve, as must every declared
        # depends_on entry and every output's source_ref.
        for calc in self.calculations:
            for ref_kind, ref_id in _iter_expression_refs(calc.expression):
                if ref_kind == "table":
                    if ref_id not in {t.id for t in self.tables}:
                        raise ValueError(
                            f"Calculation '{calc.id}' references unknown table '{ref_id}'."
                        )
                elif ref_id not in node_ids:
                    raise ValueError(
                        f"Calculation '{calc.id}' references unresolved node '{ref_id}'."
                    )
            for dep in calc.depends_on:
                if dep not in node_ids:
                    raise ValueError(
                        f"Calculation '{calc.id}' depends_on unresolved node '{dep}'."
                    )

        for output in self.outputs:
            if output.source_ref not in node_ids:
                raise ValueError(
                    f"PricingOutputV2 '{output.id}' references nonexistent source node "
                    f"'{output.source_ref}'."
                )

        # 3. Calculation-cycle detection over a *derived* dependency graph:
        # edges come from declared depends_on UNION references discovered
        # inside the expression tree itself, so a cycle hidden purely inside
        # expression references (with no matching depends_on entry) is still
        # caught, not just explicitly declared cycles.
        calc_ids = {c.id for c in self.calculations}
        graph = nx.DiGraph()
        graph.add_nodes_from(calc_ids)
        for calc in self.calculations:
            referenced = {
                ref_id
                for ref_kind, ref_id in _iter_expression_refs(calc.expression)
                if ref_kind == "node"
            }
            for dep in set(calc.depends_on) | referenced:
                if dep in calc_ids:
                    graph.add_edge(dep, calc.id)
        if not nx.is_directed_acyclic_graph(graph):
            cycle = nx.find_cycle(graph)
            raise ValueError(f"Calculation graph contains a dependency cycle: {cycle}")

        # 4. Range gap / ambiguous-overlap validation (shared with v0.1 via
        # app.ipir.tables.validate_range_coverage).
        for table in self.tables:
            issues = validate_range_coverage(table)
            if issues:
                raise ValueError("; ".join(issues))

        # 5. Output rounding-contract consistency: if an output's source
        # calculation rounds inline via a top-level RoundExpression, the
        # output's declared scale/rounding_mode must match it exactly, so
        # the published contract can never silently diverge from actual
        # rounding behavior.
        calc_by_id = {c.id: c for c in self.calculations}
        for output in self.outputs:
            calc = calc_by_id.get(output.source_ref)
            if calc is not None and isinstance(calc.expression, RoundExpression):
                top = calc.expression
                if top.scale != output.scale or top.rounding_mode != output.rounding_mode:
                    raise ValueError(
                        f"PricingOutputV2 '{output.id}' declares scale={output.scale}/"
                        f"rounding_mode={output.rounding_mode.value}, but its source "
                        f"calculation '{calc.id}' rounds to scale={top.scale}/"
                        f"rounding_mode={top.rounding_mode.value}."
                    )

        return self
