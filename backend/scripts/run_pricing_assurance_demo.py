import sys
from pathlib import Path

# Ensure backend root is in sys.path when script is executed directly
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.engines.reconciliation import PricingAssuranceRunner  # noqa: E402
from app.ipir.package import IPIRPackage  # noqa: E402


def main() -> None:
    root_dir = Path(__file__).resolve().parent.parent.parent
    canonical_file = (
        root_dir / "data" / "implementations" / "canonical" / "AZ_HO3_2026_09_ipir.json"
    )
    defective_file = (
        root_dir / "data" / "implementations" / "defective" / "AZ_HO3_2026_09_ipir.json"
    )

    if not canonical_file.exists() or not defective_file.exists():
        print("Error: IPIR package files not found.")
        sys.exit(1)

    with open(canonical_file, encoding="utf-8") as f:
        canonical_pkg = IPIRPackage.model_validate_json(f.read())

    with open(defective_file, encoding="utf-8") as f:
        defective_pkg = IPIRPackage.model_validate_json(f.read())

    # Execute full assurance pipeline
    runner = PricingAssuranceRunner()
    run = runner.run_assurance(canonical_pkg, defective_pkg)

    print("\n========================================================")
    print("           RateGuard Hardened Pricing Assurance         ")
    print("========================================================\n")
    print(f"Canonical Rate Plan: {run.test_plan.package_id}")
    print(f"Target Rating Engine: {run.test_plan.compared_package_ids[1]}")
    print(f"Overall Status:      {run.overall_status}\n")

    print("Test Planning & Risk-Directed Optimization")
    print("--------------------------------------------------------")
    print(f"Candidate Scenarios Generated: {run.test_plan.candidate_count}")
    print(f"Selected Scenarios Executed:   {run.test_plan.selected_count}")
    print(f"Candidate Reduction:           {run.candidate_reduction_pct}%\n")

    print("Selected Test Scenario Results")
    print("--------------------------------------------------------")
    for res in run.execution_results:
        status_tag = f"[{res.status:<8}]"
        print(f"{status_tag} Scenario {res.scenario_id}: {res.scenario_name}")
        exp_str = f"${res.expected_premium:<9}"
        act_str = f"${res.actual_premium:<9}"
        var_str = f"${res.absolute_variance}"
        print(f"           Expected: {exp_str} Target: {act_str} (Variance: {var_str})")

        if not res.premium_matches and res.root_cause:
            print(f"           First Divergence: {res.first_divergent_node}")
            print(f"           Root Cause:       {res.root_cause.title}")
            print(f"           Explanation:      {res.root_cause.explanation}")
        print("")

    print("Assurance Run Metrics & Coverage Summary")
    print("--------------------------------------------------------")
    print(f"  Control Scenarios Passed (Match):    {run.passed_count}")
    print(f"  Defect Scenarios Failed (Mismatch):  {run.failed_count}")
    print(f"  Semantic Difference Coverage:        {run.semantic_difference_coverage_pct}%")
    print(f"  Behavioral Difference Coverage:      {run.behavioral_difference_coverage_pct}%")
    print(f"  Premium Reproduction Rate:           {run.premium_difference_reproduction_rate_pct}%")
    print(f"  Unique Root Causes Discovered:       {len(run.discovered_root_causes)}")
    print("========================================================\n")


if __name__ == "__main__":
    main()
