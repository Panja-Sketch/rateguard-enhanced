from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.engines.diff.enums import DifferenceType
from app.engines.diff.models import SemanticDiffResult
from app.engines.impact.models import ImpactAnalysis
from app.engines.testing.boundary_generator import generate_range_boundary_values
from app.engines.testing.models import PricingTestScenario, ScenarioClassification
from app.ipir.enums import ComparisonOperator, InputDataType
from app.ipir.package import IPIRPackage


def _derive_base_risk(package: IPIRPackage) -> dict[str, Any]:
    """Builds a representative baseline risk profile directly from the
    package's own declared inputs, rather than assuming every compared
    package is an Arizona Homeowners HO3 policy. A numeric input gets the
    midpoint of its declared range (or whichever bound is present, or 0 if
    neither is), a boolean gets False, and a category/string input gets its
    first allowed value (or a neutral placeholder). DATE inputs are excluded
    -- a scenario's own `effective_date` covers temporal probing already."""
    base_risk: dict[str, Any] = {}
    for inp in package.inputs:
        if inp.allowed_values:
            # An enumerated allow-list constrains the value regardless of the
            # declared data type (e.g. a MONEY/INTEGER field like `deductible`
            # can still only take specific tiers such as 500/1000/2500/5000)
            # -- this must be checked before any type-specific fallback.
            raw = inp.allowed_values[0]
            if inp.data_type == InputDataType.INTEGER:
                base_risk[inp.id] = int(raw)
            elif inp.data_type in (InputDataType.DECIMAL, InputDataType.MONEY):
                base_risk[inp.id] = Decimal(raw)
            else:
                base_risk[inp.id] = raw
        elif inp.data_type == InputDataType.BOOLEAN:
            base_risk[inp.id] = False
        elif inp.data_type in (InputDataType.INTEGER, InputDataType.DECIMAL, InputDataType.MONEY):
            if inp.minimum is not None and inp.maximum is not None:
                mid = (Decimal(str(inp.minimum)) + Decimal(str(inp.maximum))) / 2
                base_risk[inp.id] = int(mid) if inp.data_type == InputDataType.INTEGER else mid
            elif inp.minimum is not None:
                base_risk[inp.id] = inp.minimum
            elif inp.maximum is not None:
                base_risk[inp.id] = inp.maximum
            else:
                base_risk[inp.id] = 0
        elif inp.data_type in (InputDataType.CATEGORY, InputDataType.STRING):
            base_risk[inp.id] = "DEFAULT"
        # DATE-type inputs intentionally excluded -- see docstring.
    return base_risk


def generate_candidate_scenarios(
    diff_result: SemanticDiffResult,
    impact: ImpactAnalysis,
    package: IPIRPackage,
) -> list[PricingTestScenario]:
    """Generates an initial candidate set of risk-directed test scenarios.

    Systematically exercises range boundaries, interior values, control baseline scenarios,
    temporal effective date boundaries, and sequence order sensitivity.
    """
    candidates: list[PricingTestScenario] = []
    counter = 1
    pkg_start = package.effective_period.start

    base_risk: dict[str, Any] = _derive_base_risk(package)

    # 1. Control Baseline Scenario
    candidates.append(
        PricingTestScenario(
            id=f"RG_CAND_{counter:03d}",
            name="Baseline Control Scenario",
            risk_values=dict(base_risk),
            effective_date=pkg_start + timedelta(days=20),
            classification=ScenarioClassification.CONTROL,
            target_difference_ids=[],
            target_node_ids=[],
            tags=["CONTROL"],
            purpose="Baseline control scenario to verify baseline pricing engine stability.",
        )
    )
    counter += 1

    # 2. Risk Predicate Boundary & Interior Scenarios
    for pred in impact.candidate_risk_predicates:
        min_val = None
        max_val = None
        field_name = None

        for clause in pred.clauses:
            field_name = clause.field
            if clause.operator.value in ("GTE", "GT"):
                min_val = clause.value
            elif clause.operator.value in ("LTE", "LT"):
                max_val = clause.value

        if field_name and (min_val is not None or max_val is not None):
            bounds = generate_range_boundary_values(min_val, max_val)
            inp_def = next((i for i in package.inputs if i.id == field_name), None)

            for label, b_val in bounds.items():
                if inp_def:
                    if inp_def.minimum is not None:
                        b_val = max(int(str(inp_def.minimum)), b_val)
                    if inp_def.maximum is not None:
                        b_val = min(int(str(inp_def.maximum)), b_val)

                risk = dict(base_risk)
                risk[field_name] = b_val
                eff_date = pkg_start + timedelta(days=20)

                is_control = label in ("just_below", "just_above")
                scenario_id = f"RG_CAND_{counter:03d}"
                counter += 1

                t_diff_ids = [
                    d.id
                    for d in diff_result.differences
                    if field_name in str(d.semantic_path)
                    or field_name in d.node_id
                    or field_name in d.description
                ]
                t_node_ids = [
                    d.node_id
                    for d in diff_result.differences
                    if field_name in str(d.semantic_path)
                    or field_name in d.node_id
                    or field_name in d.description
                ]

                classification = (
                    ScenarioClassification.CONTROL
                    if is_control
                    else ScenarioClassification.BOUNDARY
                )
                tag_label = "CONTROL" if is_control else "BOUNDARY"

                candidates.append(
                    PricingTestScenario(
                        id=scenario_id,
                        name=f"Predicate Boundary Scenario ({field_name}={b_val} [{label}])",
                        risk_values=risk,
                        effective_date=eff_date,
                        classification=classification,
                        target_difference_ids=t_diff_ids,
                        target_node_ids=t_node_ids,
                        tags=[tag_label, f"VAL_{b_val}"],
                        purpose=(
                            f"Test predicate boundary for {field_name} at "
                            f"value {b_val} ({label})."
                        ),
                    )
                )

    # 2b. Isolated Single-Defect Witness Scenarios (EXACT-match / global
    # predicates -- range predicates already get boundary coverage above).
    # A predicate with no clauses at all (a flat fee or premium constraint
    # change with no risk-based eligibility, see
    # app.engines.impact.predicates._global_predicate) has nothing to set --
    # the baseline control scenario is already its witness, so it only needs
    # to be tagged to that diff, not a whole new scenario.
    #
    # Every EXACT/EQ-clause predicate not already covered by a range
    # boundary gets exactly one isolated scenario: its own clause fields set
    # to the matching value, every other field left at base_risk's default.
    # Those defaults (first allowed_value, False, or a range midpoint) are
    # deliberately generic/neutral, so in practice this scenario does not
    # also happen to satisfy a different predicate -- true isolation is not
    # guaranteed against arbitrary inputs, but is the common, expected case.
    exact_predicates = [
        pred for pred in impact.candidate_risk_predicates
        if pred.clauses and all(c.operator == ComparisonOperator.EQ for c in pred.clauses)
    ]
    combined_overrides: dict[str, Any] = {}

    for pred in exact_predicates:
        risk = dict(base_risk)
        for clause in pred.clauses:
            risk[clause.field] = clause.value
            combined_overrides[clause.field] = clause.value

        target_diff_ids = [d.id for d in diff_result.differences if f"pred_{d.id}" == pred.id]
        target_nodes = [d.node_id for d in diff_result.differences if f"pred_{d.id}" == pred.id]
        field_summary = ", ".join(f"{c.field}={c.value}" for c in pred.clauses)

        candidates.append(
            PricingTestScenario(
                id=f"RG_CAND_{counter:03d}",
                name=f"Isolated Single-Defect Scenario ({field_summary})",
                risk_values=risk,
                effective_date=pkg_start + timedelta(days=20),
                classification=ScenarioClassification.SINGLE_DEFECT,
                target_difference_ids=target_diff_ids,
                target_node_ids=target_nodes,
                tags=["SINGLE_DEFECT"],
                purpose=f"Isolate the effect of {field_summary} without any other material difference active.",
            )
        )
        counter += 1

    # Every no-clause (global) predicate is already witnessed by the
    # Baseline Control Scenario -- tag it there too so revalidation/coverage
    # reporting can see the diff was exercised, without a duplicate scenario.
    global_diff_ids = [
        d.id
        for d in diff_result.differences
        for pred in impact.candidate_risk_predicates
        if pred.id == f"pred_{d.id}" and not pred.clauses and pred.temporal_start is None
    ]
    if global_diff_ids:
        candidates[0].target_difference_ids = sorted(set(candidates[0].target_difference_ids) | set(global_diff_ids))

    # 2c. Combined Multi-Defect Scenario -- one policy satisfying every
    # EXACT/EQ-clause predicate at once, so an overlap between (for example)
    # a deductible-tier defect and a multi-policy-eligibility defect is
    # actually exercised and counted, not just asserted possible.
    if len(exact_predicates) > 1:
        combined_risk = dict(base_risk)
        combined_risk.update(combined_overrides)
        all_target_diff_ids = [
            d.id for d in diff_result.differences
            if any(f"pred_{d.id}" == pred.id for pred in exact_predicates)
        ]
        all_target_nodes = [
            d.node_id for d in diff_result.differences
            if any(f"pred_{d.id}" == pred.id for pred in exact_predicates)
        ]
        candidates.append(
            PricingTestScenario(
                id=f"RG_CAND_{counter:03d}",
                name="Combined Multi-Defect Scenario",
                risk_values=combined_risk,
                effective_date=pkg_start + timedelta(days=20),
                classification=ScenarioClassification.MULTI_DEFECT,
                target_difference_ids=all_target_diff_ids,
                target_node_ids=all_target_nodes,
                tags=["MULTI_DEFECT"],
                purpose="Verify behavior for a policy where multiple material differences overlap simultaneously.",
            )
        )
        counter += 1

    # 3. Temporal Effective Date Shift Scenarios
    date_diffs = [
        d
        for d in diff_result.differences
        if d.difference_type == DifferenceType.EFFECTIVE_DATE_CHANGE
    ]
    for diff in date_diffs:
        target_start = None
        if diff.right_value:
            try:
                target_start = date.fromisoformat(str(diff.right_value))
            except ValueError:
                pass

        if target_start and target_start > pkg_start:
            pre_drift_date = pkg_start + timedelta(days=5)
            post_drift_date = target_start + timedelta(days=5)

            candidates.append(
                PricingTestScenario(
                    id=f"RG_CAND_{counter:03d}",
                    name=f"Temporal Effective Date Drift Pre-Window ({diff.node_id})",
                    risk_values=dict(base_risk),
                    effective_date=pre_drift_date,
                    classification=ScenarioClassification.TEMPORAL,
                    target_difference_ids=[diff.id],
                    target_node_ids=[diff.node_id],
                    tags=["TEMPORAL", "PRE_WINDOW"],
                    purpose=(
                        f"Verify pricing behavior prior to delayed "
                        f"effective date {target_start}."
                    ),
                )
            )
            counter += 1

            candidates.append(
                PricingTestScenario(
                    id=f"RG_CAND_{counter:03d}",
                    name=f"Temporal Effective Date Drift Post-Window ({diff.node_id})",
                    risk_values=dict(base_risk),
                    effective_date=post_drift_date,
                    classification=ScenarioClassification.TEMPORAL,
                    target_difference_ids=[diff.id],
                    target_node_ids=[diff.node_id],
                    tags=["TEMPORAL", "POST_WINDOW"],
                    purpose=f"Verify pricing behavior after delayed effective date {target_start}.",
                )
            )
            counter += 1

    # 4. Calculation Sequence Order Mismatch Scenarios
    order_diffs = [
        d for d in diff_result.differences if d.difference_type == DifferenceType.ORDER_CHANGE
    ]
    if order_diffs:
        seq_risk = dict(base_risk)
        # These specific overrides are tuned to push an HO3-shaped policy
        # past its minimum-premium floor; only apply them when the package
        # actually declares these fields, so an unrelated product's risk
        # profile never gets HO3-specific values injected into it.
        for field, override_val in {
            "dwelling_limit": 700000,
            "multi_policy": False,
            "claims_free": False,
        }.items():
            if field in seq_risk:
                seq_risk[field] = override_val

        candidates.append(
            PricingTestScenario(
                id=f"RG_CAND_{counter:03d}",
                name="Sequence Order Pre-Minimum Floor Scenario",
                risk_values=seq_risk,
                effective_date=pkg_start + timedelta(days=20),
                classification=ScenarioClassification.CONSTRAINT_ORDER,
                target_difference_ids=[d.id for d in order_diffs],
                target_node_ids=[d.node_id for d in order_diffs],
                tags=["SEQUENCE", "ORDER_MISMATCH"],
                purpose="Isolate order dependency between minimum premium floor and fee addition.",
            )
        )
        counter += 1

    return candidates
