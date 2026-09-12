from decimal import Decimal

from app.engines.diff.models import SemanticDiffResult
from app.engines.oracle.models import OracleResult
from app.engines.reconciliation.models import PremiumReconciliationResult
from app.engines.reconciliation.rca import perform_root_cause_analysis
from app.engines.reconciliation.trace_alignment import align_and_compare_traces
from app.engines.target.models import TargetQuoteResult
from app.engines.testing.models import PricingTestScenario
from app.ipir.package import IPIRPackage


class ReconciliationEngine:
    """Reconciles expected Oracle results against actual target rating quote results."""

    def reconcile(
        self,
        scenario: PricingTestScenario,
        expected: OracleResult,
        actual: TargetQuoteResult,
        diff_result: SemanticDiffResult,
        package: IPIRPackage,
    ) -> PremiumReconciliationResult:
        """Reconciles expected vs actual premium, trace steps, and derives root cause findings."""
        exp_prem = expected.final_premium
        act_prem = actual.final_premium
        abs_var = abs(exp_prem - act_prem)
        pct_var = (
            (abs_var / exp_prem * Decimal("100")) if exp_prem != Decimal("0") else Decimal("0")
        )
        premium_matches = exp_prem == act_prem

        trace_diffs = align_and_compare_traces(expected.trace, actual.trace, package)
        trace_diverged = len(trace_diffs) > 0

        first_divergent, root_cause, contributing = perform_root_cause_analysis(
            trace_diffs, diff_result, package
        )

        related_diff_ids = [td.node_id for td in trace_diffs]

        return PremiumReconciliationResult(
            scenario_id=scenario.id,
            scenario_name=scenario.name,
            expected_premium=exp_prem,
            actual_premium=act_prem,
            absolute_variance=abs_var,
            percentage_variance=round(pct_var, 4),
            premium_matches=premium_matches,
            trace_diverged=trace_diverged,
            trace_differences=trace_diffs,
            first_divergent_node=first_divergent,
            root_cause=root_cause,
            contributing_root_causes=contributing,
            related_semantic_difference_ids=related_diff_ids,
            status="MATCH" if premium_matches else "MISMATCH",
            metadata={"currency": expected.currency},
        )
