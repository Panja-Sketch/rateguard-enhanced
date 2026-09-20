"""Deterministic, package-derived probe generation for the real mission path.

A connector-backed (or otherwise diff-less) mission has no semantic difference
to steer test selection, so probes must come from the compiled Source A
package itself:

1. **Control cases** -- the workbook's own golden input/output examples that
   already passed verification at compile time.
2. **Baseline** -- only when there is no control case: a policy built from
   *declared* input defaults (never a silent all-zero policy; if any input has
   no declared basis, no baseline is fabricated).
3. **Boundaries** -- for every rate-table range bound, exact-match key and
   input-vs-literal rule condition in the compiled IPIR, the value and its
   immediate neighbours (e.g. ``roof_age >= 21`` yields 20, 21 and 22), with
   every non-target input taken from a valid control case (or the declared
   defaults) and every value kept inside the input's declared domain.

Mutation-targeted probes (known structural differences) are produced by
``candidate_generator`` and merged by the planner; this module supplies their
base policy too.

Every probe is verified to be executable by the oracle before it is returned,
carries provenance describing where it came from, is de-duplicated on its
complete (inputs, date, transaction type) identity, and is ordered
deterministically. Probes that cannot execute are reported in ``skipped``
rather than silently dropped.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from math import ceil, floor
from typing import Any

from app.engines.oracle.calculation_date import CalculationDateSource, resolve_calculation_date
from app.engines.oracle.errors import CalculationDateError, OracleError
from app.engines.oracle.evaluator import evaluate_package
from app.engines.oracle.models import RiskInput
from app.engines.testing.models import PricingTestScenario, ScenarioClassification
from app.ipir.enums import InputDataType, TransactionType
from app.ipir.inputs import PricingInput
from app.ipir.package import IPIRPackage
from app.ipir.rules import ComparisonCondition, LogicalCondition
from app.ipir.tables import ExactMatch, RangeMatch
from app.ipir.v0_2.control_cases import ControlCase

DEFAULT_MAX_PROBES = 15
_DATE_TYPES = (InputDataType.DATE,) if hasattr(InputDataType, "DATE") else ()
_CONTEXT_KEYS = ("effective_date", "transaction_type")


class ProbeOrigin(StrEnum):
    CONTROL_CASE = "CONTROL_CASE"
    BASELINE = "BASELINE"
    BOUNDARY = "BOUNDARY"
    MUTATION = "MUTATION"


@dataclass
class PackageProbeResult:
    scenarios: list[PricingTestScenario] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    base_risk: dict[str, Any] | None = None
    base_date: date | None = None
    base_date_source: CalculationDateSource | None = None


def canonical_probe_key(risk: dict[str, Any], effective_date: date, txn: TransactionType) -> tuple:
    """Identity used to de-duplicate equivalent probes (the same policy on the
    same date and transaction type, regardless of dict order or 25 vs 25.00)."""
    items = []
    for k in sorted(risk):
        v = risk[k]
        if isinstance(v, bool) or not isinstance(v, (int, Decimal)):
            items.append((k, str(v)))
        else:
            items.append((k, str(Decimal(v).normalize())))
    return (tuple(items), effective_date.isoformat(), txn.value)


# ---------------------------------------------------------------- typing helpers


def _to_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _coerce(inp: PricingInput, raw: Any) -> Any | None:
    """Coerces a raw value to the Python type the input declares; None when it
    cannot be represented faithfully."""
    if inp.data_type == InputDataType.BOOLEAN:
        return raw if isinstance(raw, bool) else None
    if inp.data_type == InputDataType.INTEGER:
        d = _to_decimal(raw)
        return int(d) if d is not None and d == d.to_integral_value() else None
    if inp.data_type in (InputDataType.DECIMAL, InputDataType.MONEY):
        return _to_decimal(raw)
    return str(raw) if raw is not None and not isinstance(raw, bool) else None


def _in_domain(inp: PricingInput, value: Any) -> bool:
    if inp.allowed_values is not None:
        allowed = {str(a) for a in inp.allowed_values}
        d = _to_decimal(value)
        norm = {str(Decimal(a).normalize()) for a in inp.allowed_values if _to_decimal(a) is not None}
        return str(value) in allowed or (d is not None and str(d.normalize()) in norm)
    d = _to_decimal(value)
    if d is None:
        return inp.data_type not in (InputDataType.INTEGER, InputDataType.DECIMAL, InputDataType.MONEY)
    if inp.minimum is not None and d < Decimal(str(inp.minimum)):
        return False
    return not (inp.maximum is not None and d > Decimal(str(inp.maximum)))


def _declared_default_risk(package: IPIRPackage) -> dict[str, Any] | None:
    """A baseline built only from what the package itself declares. Returns
    None (no baseline) when any rating input has no declared basis for a
    value, instead of inventing zeros or placeholders."""
    risk: dict[str, Any] = {}
    for inp in package.inputs:
        if inp.data_type in _DATE_TYPES:
            continue
        if inp.allowed_values:
            value = _coerce(inp, inp.allowed_values[0])
        elif inp.data_type == InputDataType.BOOLEAN:
            value = False
        elif inp.data_type in (InputDataType.INTEGER, InputDataType.DECIMAL, InputDataType.MONEY):
            if inp.minimum is not None and inp.maximum is not None:
                mid = (Decimal(str(inp.minimum)) + Decimal(str(inp.maximum))) / 2
                value = _coerce(inp, mid if inp.data_type != InputDataType.INTEGER else int(mid))
            elif inp.minimum is not None:
                value = _coerce(inp, inp.minimum)
            elif inp.maximum is not None:
                value = _coerce(inp, inp.maximum)
            else:
                return None
        else:
            return None
        if value is None:
            return None
        risk[inp.id] = value
    return risk


# ---------------------------------------------------------------- boundaries


def _walk_conditions(cond: ComparisonCondition | LogicalCondition, out: list[ComparisonCondition]) -> None:
    if isinstance(cond, ComparisonCondition):
        out.append(cond)
    else:
        for child in cond.conditions:
            _walk_conditions(child, out)


def _literal_and_ref(cond: ComparisonCondition, input_ids: set[str]) -> tuple[str, Any] | None:
    from app.ipir.common import LiteralValue, NodeReference

    if isinstance(cond.left, NodeReference) and cond.left.ref in input_ids and isinstance(cond.right, LiteralValue):
        return cond.left.ref, cond.right.value
    if isinstance(cond.right, NodeReference) and cond.right.ref in input_ids and isinstance(cond.left, LiteralValue):
        return cond.right.ref, cond.left.value
    return None


def collect_boundaries(package: IPIRPackage) -> dict[str, dict[Any, str]]:
    """{input_id: {raw_value: source description}} from compiled tables and
    rule conditions. Deterministic: sorted by the caller."""
    by_id = {i.id: i for i in package.inputs}
    found: dict[str, dict[Any, str]] = {}

    def add(input_id: str, value: Any, source: str) -> None:
        found.setdefault(input_id, {}).setdefault(value, source)

    for table in package.tables:
        for dim_idx, dim in enumerate(table.dimensions):
            if dim.input_ref not in by_id:
                continue
            for row_idx, row in enumerate(table.rows):
                if dim_idx >= len(row.matches):
                    continue
                match = row.matches[dim_idx]
                if isinstance(match, RangeMatch):
                    for bound in ("minimum", "maximum"):
                        raw = getattr(match, bound)
                        if raw is not None:
                            add(dim.input_ref, raw, f"table:{table.id}:row{row_idx + 1}:{bound}")
                elif isinstance(match, ExactMatch):
                    add(dim.input_ref, match.value, f"table:{table.id}:row{row_idx + 1}:exact")

    input_ids = set(by_id)
    for rule in package.rules:
        comparisons: list[ComparisonCondition] = []
        _walk_conditions(rule.condition, comparisons)
        for cond in comparisons:
            hit = _literal_and_ref(cond, input_ids)
            if hit is not None:
                add(hit[0], hit[1], f"rule:{rule.id}:{cond.operator.value}")
    return found


def _neighbours(inp: PricingInput, raw: Any) -> list[tuple[Any, int]]:
    """(value, tier) candidates for one boundary: tier 1 is the boundary value
    itself, tier 2 its immediate neighbours. Values are typed and in-domain."""
    d = _to_decimal(raw)
    numeric_type = inp.data_type in (InputDataType.INTEGER, InputDataType.DECIMAL, InputDataType.MONEY)
    if d is None or not numeric_type:
        typed = _coerce(inp, raw)
        return [(typed, 1)] if typed is not None and _in_domain(inp, typed) else []

    if inp.data_type == InputDataType.INTEGER:
        raw_points = [(floor(d), 1), (ceil(d), 1), (floor(d) - 1, 2), (ceil(d) + 1, 2)]
        if floor(d) == ceil(d):
            raw_points = [(int(d), 1), (int(d) - 1, 2), (int(d) + 1, 2)]
    else:
        step = Decimal("0.01")
        raw_points = [(d, 1), (d - step, 2), (d + step, 2)]

    out: list[tuple[Any, int]] = []
    for value, tier in raw_points:
        typed = _coerce(inp, value)
        if typed is not None and _in_domain(inp, typed):
            out.append((typed, tier))
    return out


# ---------------------------------------------------------------- generation


def _scenario(
    *, name: str, purpose: str, risk: dict[str, Any], on: date, txn: TransactionType,
    origin: ProbeOrigin, date_source: CalculationDateSource, provenance: dict[str, Any],
    classification: ScenarioClassification, tags: list[str],
) -> PricingTestScenario:
    return PricingTestScenario(
        id="RG_PKG_TMP",
        name=name,
        risk_values=risk,
        effective_date=on,
        transaction_type=txn,
        purpose=purpose,
        classification=classification,
        tags=tags,
        metadata={
            "probe_origin": origin.value,
            "calculation_date": on.isoformat(),
            "calculation_date_source": date_source.value,
            "provenance": provenance,
        },
    )


def generate_package_probes(
    package: IPIRPackage,
    control_cases: Sequence[ControlCase] = (),
    *,
    limit: int = DEFAULT_MAX_PROBES,
) -> PackageProbeResult:
    result = PackageProbeResult()
    by_id = {i.id: i for i in package.inputs}
    required = {i.id for i in package.inputs if i.required and i.data_type not in _DATE_TYPES}
    seen: set[tuple] = set()
    control_probes: list[PricingTestScenario] = []

    def executable(risk: dict[str, Any], on: date, txn: TransactionType) -> str | None:
        try:
            # The evaluator itself (not a calculator wrapper): executability is
            # a pure property of (package, inputs, date, transaction type).
            evaluate_package(package, RiskInput(values=dict(risk)), on, txn)
        except (OracleError, ValueError) as exc:
            return f"{type(exc).__name__}: {exc}"
        return None

    # 1. Valid control cases -------------------------------------------------
    for case in control_cases:
        raw_inputs = dict(case.inputs)
        raw_date = raw_inputs.pop("effective_date", None)
        raw_txn = raw_inputs.pop("transaction_type", None)
        try:
            resolved = resolve_calculation_date(package, control_case=raw_date)
            txn = TransactionType(raw_txn) if raw_txn else package.transaction_types[0]
        except (CalculationDateError, ValueError) as exc:
            result.skipped.append({"origin": "CONTROL_CASE", "ref": case.case_id, "reason": str(exc)})
            continue
        typed: dict[str, Any] = {}
        problem = None
        for key, raw in raw_inputs.items():
            inp = by_id.get(key)
            value = _coerce(inp, raw) if inp is not None else None
            if inp is None or value is None or not _in_domain(inp, value):
                problem = f"input '{key}' is unknown, unrepresentable or outside its declared domain"
                break
            typed[key] = value
        if problem is None and not required <= set(typed):
            problem = f"missing required inputs {sorted(required - set(typed))}"
        if problem is None:
            problem = executable(typed, resolved.value, txn)
        if problem is not None:
            result.skipped.append({"origin": "CONTROL_CASE", "ref": case.case_id, "reason": problem})
            continue
        key_ = canonical_probe_key(typed, resolved.value, txn)
        if key_ in seen:
            continue
        seen.add(key_)
        control_probes.append(_scenario(
            name=f"Workbook Control Case ({case.case_id})",
            purpose=f"Reproduce workbook control case '{case.case_id}' with its expected outputs.",
            risk=typed, on=resolved.value, txn=txn, origin=ProbeOrigin.CONTROL_CASE,
            date_source=resolved.source,
            provenance={"control_case_id": case.case_id, "expected_outputs": dict(case.expected_outputs)},
            classification=ScenarioClassification.CONTROL, tags=["CONTROL", "CONTROL_CASE"],
        ))

    # 2. Base policy for non-target fields ------------------------------------
    baseline_probe: PricingTestScenario | None = None
    if control_probes:
        first = control_probes[0]
        result.base_risk = dict(first.risk_values)
        result.base_date = first.effective_date
        result.base_date_source = CalculationDateSource(first.metadata["calculation_date_source"])
    else:
        defaults = _declared_default_risk(package)
        if defaults is None:
            result.skipped.append({
                "origin": "BASELINE", "ref": "declared_defaults",
                "reason": "At least one input has no declared default, bound or allowed value and no valid "
                          "control case exists; no baseline policy was fabricated.",
            })
        else:
            try:
                resolved = resolve_calculation_date(package)
                txn = package.transaction_types[0]
            except CalculationDateError as exc:
                result.skipped.append({"origin": "BASELINE", "ref": "declared_defaults", "reason": str(exc)})
            else:
                problem = executable(defaults, resolved.value, txn)
                if problem:
                    result.skipped.append({"origin": "BASELINE", "ref": "declared_defaults", "reason": problem})
                else:
                    result.base_risk, result.base_date = dict(defaults), resolved.value
                    result.base_date_source = resolved.source
                    seen.add(canonical_probe_key(defaults, resolved.value, txn))
                    baseline_probe = _scenario(
                        name="Baseline Control Scenario (declared defaults)",
                        purpose="Baseline policy built only from the package's declared input defaults.",
                        risk=dict(defaults), on=resolved.value, txn=txn, origin=ProbeOrigin.BASELINE,
                        date_source=resolved.source, provenance={"basis": "declared_input_defaults"},
                        classification=ScenarioClassification.CONTROL, tags=["CONTROL", "BASELINE"],
                    )

    ordered: list[PricingTestScenario] = list(control_probes)
    if baseline_probe is not None:
        ordered.append(baseline_probe)

    # 3. Boundary probes --------------------------------------------------------
    if result.base_risk is not None and result.base_date is not None:
        txn0 = package.transaction_types[0]
        candidates: list[tuple[int, int, str, Decimal, str, Any, str, Any]] = []
        for input_id, points in sorted(collect_boundaries(package).items()):
            inp = by_id[input_id]
            for raw, source in points.items():
                for value, tier in _neighbours(inp, raw):
                    numeric = _to_decimal(value)
                    # Numeric range/condition boundaries outrank enumerations of
                    # exact-match keys when the probe limit forces a choice.
                    kind = 0 if (numeric is not None and not source.endswith(":exact")) else 1
                    candidates.append((kind, tier, input_id, numeric if numeric is not None else Decimal(0),
                                       str(value), value, source, raw))
        # Deterministic and fair under a probe limit: within each input, numeric
        # boundaries come before exact-match keys and boundary values before
        # their neighbours; inputs are then interleaved round-robin (by input
        # id) so no single input's enumeration starves another input's
        # boundaries.
        per_input: dict[str, list] = {}
        for cand in sorted(candidates, key=lambda c: (c[2], c[0], c[1], c[3], c[4])):
            per_input.setdefault(cand[2], []).append(cand)
        interleaved = []
        for rank in range(max((len(v) for v in per_input.values()), default=0)):
            for input_id_ in sorted(per_input):
                if rank < len(per_input[input_id_]):
                    interleaved.append(per_input[input_id_][rank])
        for _kind, tier, input_id, _num, _txt, value, source, raw in interleaved:
            if len(ordered) >= limit:
                break
            risk = dict(result.base_risk)
            risk[input_id] = value
            key_ = canonical_probe_key(risk, result.base_date, txn0)
            if key_ in seen:
                continue
            problem = executable(risk, result.base_date, txn0)
            if problem is not None:
                result.skipped.append({"origin": "BOUNDARY", "ref": f"{input_id}={value}", "reason": problem})
                continue
            seen.add(key_)
            ordered.append(_scenario(
                name=f"Boundary Probe ({input_id}={value})",
                purpose=f"Exercise {input_id}={value} at/adjacent to boundary {raw} ({source}).",
                risk=risk, on=result.base_date, txn=txn0, origin=ProbeOrigin.BOUNDARY,
                date_source=result.base_date_source or CalculationDateSource.PACKAGE_EFFECTIVE_START,
                provenance={"input": input_id, "boundary_value": str(raw), "boundary_source": source,
                            "tier": "AT_BOUNDARY" if tier == 1 else "ADJACENT"},
                classification=ScenarioClassification.BOUNDARY,
                tags=["BOUNDARY", f"VAL_{value}", f"INPUT_{input_id}"],
            ))

    result.scenarios = ordered[:limit]
    return result
