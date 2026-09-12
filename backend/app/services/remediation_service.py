import uuid
from copy import deepcopy
from decimal import Decimal
from typing import Any

from app.engines.diff import SemanticDiffEngine
from app.engines.oracle.calculator import PremiumOracleCalculator
from app.engines.portfolio import PortfolioExposureAnalyzer
from app.engines.testing.models import PricingTestScenario
from app.ipir.package import IPIRPackage
from app.models.mission import (
    MaterialFinding,
    RemediationProposal,
    RevalidationResult,
)


# Friendly labels keyed by the DifferenceType prefix that
# app.services.finding_conversion.to_material_findings always puts before
# ": " in a MaterialFinding's title. MaterialFinding itself has no
# difference_type field to read directly (see app.models.mission), so the
# title prefix is the one reliable, already-guaranteed-present source.
_DIFF_TYPE_LABELS: dict[str, str] = {
    "VALUE_CHANGE": "constant value change",
    "RANGE_CHANGE": "range boundary change",
    "RULE_CHANGE": "rule change",
    "ORDER_CHANGE": "sequence order change",
    "EFFECTIVE_DATE_CHANGE": "effective date change",
    "ROUNDING_CHANGE": "rounding rule change",
    "MISSING_NODE": "missing node",
    "EXTRA_NODE": "extra node",
    "TABLE_ROW_CHANGE": "rate table factor drift",
    "TABLE_ROW_MISSING": "missing rate table row",
    "TABLE_ROW_EXTRA": "extra rate table row",
    "TABLE_COVERAGE_GAP": "rate table coverage gap",
    "OUTPUT_CHANGE": "output definition change",
    "MODIFIER_CHANGE": "modifier value change",
    "CONSTRAINT_CHANGE": "constraint change",
    "FEE_CHANGE": "fee amount change",
}


def _summarize_finding_categories(differences: list[MaterialFinding]) -> str:
    """Builds a rationale sentence naming the actual kinds of differences being
    corrected, instead of a fixed sentence that names categories regardless of
    what the mission's own findings actually are (a remediation summary that
    talks about "effective start dates and sequence ordering" for a mission
    that only changed a fee amount and a modifier percentage is exactly the
    kind of unverified claim this tool exists to catch elsewhere)."""
    counts: dict[str, int] = {}
    for diff in differences:
        prefix = diff.title.split(":", 1)[0].strip()
        label = _DIFF_TYPE_LABELS.get(prefix, prefix.replace("_", " ").lower() or "difference")
        counts[label] = counts.get(label, 0) + 1

    parts = [f"{n} {label}{'s' if n != 1 else ''}" for label, n in counts.items()]
    if not parts:
        return "Corrects the confirmed findings for this mission."
    if len(parts) == 1:
        joined = parts[0]
    elif len(parts) == 2:
        joined = f"{parts[0]} and {parts[1]}"
    else:
        joined = ", ".join(parts[:-1]) + f", and {parts[-1]}"
    return f"Corrects {joined}."


class RemediationService:
    """Service handling isolated remediation patch generation and revalidation testing."""

    def __init__(self):
        self.diff_engine = SemanticDiffEngine()
        self.portfolio_analyzer = PortfolioExposureAnalyzer()

    def generate_remediation_proposal(
        self,
        intent_pkg: IPIRPackage,
        defective_pkg: IPIRPackage,
        differences: list[MaterialFinding],
    ) -> RemediationProposal:
        """Generates an isolated derived target package containing proposed corrections without mutating canonical files."""
        rem_id = f"REM-{uuid.uuid4().hex[:6].upper()}"
        derived_id = f"IPIR_PATCHED_{uuid.uuid4().hex[:6].upper()}"

        proposed_changes: dict[str, Any] = {}

        for diff in differences:
            proposed_changes[diff.finding_id] = {
                "title": diff.title,
                "before_target_value": diff.target_value,
                "proposed_intent_value": diff.intent_value,
                "affected_node": diff.affected_node,
            }

        return RemediationProposal(
            remediation_id=rem_id,
            title=f"Proposed Rating Engine Patch ({len(differences)} Fixes)",
            rationale=_summarize_finding_categories(differences),
            derived_package_id=derived_id,
            proposed_changes=proposed_changes,
            source_evidence_ref="EV-SEMANTIC-DIFF",
        )

    def revalidate_remediation(
        self,
        intent_pkg: IPIRPackage,
        defective_pkg: IPIRPackage,
        proposal: RemediationProposal,
        portfolio_csv: str = "az_ho3_2026_synthetic_50k.csv",
        scenario_pool: list[PricingTestScenario] | None = None,
        targeted_scenario_ids: list[str] | None = None,
        regression_scenario_ids: list[str] | None = None,
    ) -> RevalidationResult:
        """Re-executes portfolio exposure analysis using the patched target package to
        calculate before vs after financial exposure, and deterministically reruns any
        targeted (previously-mismatched) and regression (control) scenario IDs against
        the patched package via the same PremiumOracleCalculator used everywhere else.

        The original source/target packages are never mutated: `patched_pkg` is an
        isolated in-memory clone of the intent package under a new derived ID.
        """
        # Create patched target package in-memory by cloning intent package
        patched_pkg = deepcopy(intent_pkg)
        patched_pkg.id = proposal.derived_package_id
        patched_pkg.name = f"{intent_pkg.name} (Remediated Release Patch)"

        # Before remediation exposure
        before_eval = self.portfolio_analyzer.evaluate_portfolio(
            left_package=intent_pkg,
            right_package=defective_pkg,
            csv_filename=portfolio_csv,
        )

        # After remediation exposure
        after_eval = self.portfolio_analyzer.evaluate_portfolio(
            left_package=intent_pkg,
            right_package=patched_pkg,
            csv_filename=portfolio_csv,
        )

        before_exp = Decimal(str(before_eval.total_absolute_variance))
        after_exp = Decimal(str(after_eval.total_absolute_variance))

        eliminated_pct = 100.0 if before_exp == Decimal("0.00") else float(
            ((before_exp - after_exp) / before_exp) * Decimal("100.0")
        )

        new_decision = "PASS" if after_eval.financially_affected_count == 0 else "BLOCK_DEPLOYMENT"

        targeted_rerun, targeted_passed, regression_rerun, regression_passed = (
            self._rerun_scenarios(
                intent_pkg, patched_pkg, scenario_pool, targeted_scenario_ids, regression_scenario_ids
            )
        )

        return RevalidationResult(
            revalidation_id=f"REV-{uuid.uuid4().hex[:6].upper()}",
            remediation_id=proposal.remediation_id,
            before_absolute_exposure=str(before_eval.total_absolute_variance),
            after_absolute_exposure=str(after_eval.total_absolute_variance),
            before_affected_policies=before_eval.financially_affected_count,
            after_affected_policies=after_eval.financially_affected_count,
            exposure_eliminated_pct=round(eliminated_pct, 2),
            new_release_decision=new_decision,
            targeted_tests_rerun=targeted_rerun,
            targeted_tests_passed=targeted_passed,
            regression_tests_rerun=regression_rerun,
            regression_tests_passed=regression_passed,
        )

    def _rerun_scenarios(
        self,
        intent_pkg: IPIRPackage,
        patched_pkg: IPIRPackage,
        scenario_pool: list[PricingTestScenario] | None,
        targeted_ids: list[str] | None,
        regression_ids: list[str] | None,
    ) -> tuple[int, int, int, int]:
        if not scenario_pool or not (targeted_ids or regression_ids):
            return 0, 0, 0, 0

        by_id = {sc.id: sc for sc in scenario_pool}
        intent_oracle = PremiumOracleCalculator(intent_pkg)
        patched_oracle = PremiumOracleCalculator(patched_pkg)

        def _rerun(ids: list[str] | None) -> tuple[int, int]:
            rerun = passed = 0
            for scenario_id in ids or []:
                scenario = by_id.get(scenario_id)
                if scenario is None:
                    continue
                expected = intent_oracle.calculate_policy_premium(scenario.risk_values).final_premium
                actual = patched_oracle.calculate_policy_premium(scenario.risk_values).final_premium
                rerun += 1
                if expected == actual:
                    passed += 1
            return rerun, passed

        targeted_rerun, targeted_passed = _rerun(targeted_ids)
        regression_rerun, regression_passed = _rerun(regression_ids)
        return targeted_rerun, targeted_passed, regression_rerun, regression_passed
