from typing import Any

from app.engines.reconciliation import PricingAssuranceRunner
from app.ipir.package import IPIRPackage


def execute_pricing_assurance_tool(
    canonical_package_json: str, target_package_json: str
) -> dict[str, Any]:
    """Deterministic tool wrapper for full target execution, reconciliation, and RCA."""
    canonical_pkg = IPIRPackage.model_validate_json(canonical_package_json)
    target_pkg = IPIRPackage.model_validate_json(target_package_json)

    runner = PricingAssuranceRunner()
    run = runner.run_assurance(canonical_pkg, target_pkg)

    return {
        "overall_status": run.overall_status,
        "passed_count": run.passed_count,
        "failed_count": run.failed_count,
        "matched_count": run.matched_count,
        "mismatch_count": run.mismatch_count,
        "candidate_reduction_pct": run.candidate_reduction_pct,
        "semantic_difference_coverage_pct": run.semantic_difference_coverage_pct,
        "behavioral_difference_coverage_pct": run.behavioral_difference_coverage_pct,
        "premium_difference_reproduction_rate_pct": run.premium_difference_reproduction_rate_pct,
        "unique_root_cause_count": len(run.discovered_root_causes),
        "execution_results": [
            {
                "scenario_id": res.scenario_id,
                "scenario_name": res.scenario_name,
                "expected_premium": str(res.expected_premium),
                "actual_premium": str(res.actual_premium),
                "absolute_variance": str(res.absolute_variance),
                "status": res.status,
                "premium_matches": res.premium_matches,
                "trace_diverged": res.trace_diverged,
                "first_divergent_node": res.first_divergent_node,
                "root_cause_title": res.root_cause.title if res.root_cause else None,
                "root_cause_explanation": res.root_cause.explanation if res.root_cause else None,
            }
            for res in run.execution_results
        ],
        "discovered_root_causes": [
            {
                "node_id": rc.node_id,
                "category": rc.category,
                "title": rc.title,
                "explanation": rc.explanation,
                "expected_value": str(rc.expected_value),
                "actual_value": str(rc.actual_value),
                "semantic_issue_group_id": rc.semantic_issue_group_id,
            }
            for rc in run.discovered_root_causes
        ],
    }
