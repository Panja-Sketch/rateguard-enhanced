from typing import Any

from app.engines.diff.models import SemanticDiffResult
from app.engines.impact import ImpactAnalyzer
from app.ipir.package import IPIRPackage


def analyze_pricing_impact_tool(
    diff_result_dict: dict[str, Any], canonical_package_json: str
) -> dict[str, Any]:
    """Deterministic tool wrapper for pricing dependency graph and impact predicate derivation."""
    canonical_pkg = IPIRPackage.model_validate_json(canonical_package_json)
    diff_res = SemanticDiffResult.model_validate(diff_result_dict)

    impact = ImpactAnalyzer().analyze(diff_res, canonical_pkg)

    return impact.model_dump(mode="json")
