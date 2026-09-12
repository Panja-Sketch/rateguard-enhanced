from typing import Any

from app.engines.diff.models import SemanticDiffResult
from app.engines.impact.models import ImpactAnalysis
from app.engines.testing import PricingTestPlanner
from app.ipir.package import IPIRPackage


def generate_assurance_test_plan_tool(
    canonical_package_json: str,
    diff_result_dict: dict[str, Any],
    impact_dict: dict[str, Any],
) -> dict[str, Any]:
    """Deterministic tool wrapper for risk-directed test plan generation."""
    canonical_pkg = IPIRPackage.model_validate_json(canonical_package_json)
    diff_res = SemanticDiffResult.model_validate(diff_result_dict)
    impact = ImpactAnalysis.model_validate(impact_dict)

    test_plan = PricingTestPlanner().generate_plan(canonical_pkg, diff_res, impact)

    return {
        "package_id": test_plan.package_id,
        "candidate_count": test_plan.candidate_count,
        "selected_count": test_plan.selected_count,
        "candidate_reduction_pct": test_plan.coverage_metrics.get("candidate_reduction_pct", 0.0),
        "coverage_metrics": test_plan.coverage_metrics,
        "selected_scenarios": [
            {
                "id": sc.id,
                "name": sc.name,
                "classification": sc.classification.value,
                "purpose": sc.purpose,
                "target_node_ids": sc.target_node_ids,
                "target_issue_group_ids": sc.target_issue_group_ids,
                "effective_date": sc.effective_date.isoformat(),
            }
            for sc in test_plan.selected_scenarios
        ],
    }
