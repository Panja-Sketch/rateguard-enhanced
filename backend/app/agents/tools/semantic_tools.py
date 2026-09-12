from typing import Any

from app.engines.diff import compare_packages
from app.ipir.package import IPIRPackage


def compare_ipir_packages_tool(left_package_json: str, right_package_json: str) -> dict[str, Any]:
    """Deterministic tool wrapper for IPIR semantic difference analysis.

    Parses left and right IPIR packages and executes the deterministic comparison engine.
    """
    left_pkg = IPIRPackage.model_validate_json(left_package_json)
    right_pkg = IPIRPackage.model_validate_json(right_package_json)

    diff_res = compare_packages(left_pkg, right_pkg)
    data = diff_res.model_dump(mode="json")

    data["summary"] = {
        "critical_count": sum(1 for d in diff_res.differences if d.severity.value == "CRITICAL"),
        "high_count": sum(1 for d in diff_res.differences if d.severity.value == "HIGH"),
        "medium_count": sum(1 for d in diff_res.differences if d.severity.value == "MEDIUM"),
    }

    return data
