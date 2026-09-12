from app.engines.diff.models import SemanticDiffResult
from app.engines.impact.models import ImpactAnalysis
from app.engines.testing.candidate_generator import generate_candidate_scenarios
from app.engines.testing.models import PricingTestPlan
from app.engines.testing.optimizer import optimize_test_plan
from app.ipir.package import IPIRPackage


class PricingTestPlanner:
    """Generates risk-directed pricing test plans deterministically from semantic diffs."""

    def generate_plan(
        self, package: IPIRPackage, diff_result: SemanticDiffResult, impact: ImpactAnalysis
    ) -> PricingTestPlan:
        """Generates candidates, scores, optimizes, and returns a structured PricingTestPlan."""
        candidates = generate_candidate_scenarios(diff_result, impact, package)
        selected = optimize_test_plan(candidates)

        diff_ids = [d.id for d in diff_result.differences]
        target_diff_ids_covered: set[str] = set()
        for sc in selected:
            target_diff_ids_covered.update(sc.target_difference_ids)

        diff_coverage_pct = (
            (len(target_diff_ids_covered) / len(diff_ids) * 100.0) if diff_ids else 100.0
        )
        candidate_reduction_pct = (
            ((len(candidates) - len(selected)) / len(candidates) * 100.0) if candidates else 0.0
        )

        return PricingTestPlan(
            package_id=package.id,
            compared_package_ids=[diff_result.left_package_id, diff_result.right_package_id],
            semantic_difference_ids=diff_ids,
            candidate_count=len(candidates),
            selected_count=len(selected),
            selected_scenarios=selected,
            candidate_scenarios=candidates,
            coverage_metrics={
                "candidate_reduction_pct": round(candidate_reduction_pct, 2),
                "semantic_difference_coverage_pct": round(diff_coverage_pct, 2),
                "differences_covered": len(target_diff_ids_covered),
                "total_differences": len(diff_ids),
            },
            planning_metadata={"planner": "RateGuard Risk-Directed Test Planner 0.1"},
        )
