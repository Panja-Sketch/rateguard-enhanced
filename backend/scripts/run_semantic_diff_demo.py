import sys
from pathlib import Path

# Ensure backend root is in sys.path when script is executed directly
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.engines.diff import compare_packages  # noqa: E402
from app.engines.impact import ImpactAnalyzer  # noqa: E402
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

    # 1. Compare packages
    diff_result = compare_packages(canonical_pkg, defective_pkg)

    print("\n========================================================")
    print("            RateGuard Semantic Assurance                ")
    print("========================================================\n")
    print(f"Canonical Package: {diff_result.left_package_id} ({diff_result.left_version})")
    print(f"Target Package:    {diff_result.right_package_id} ({diff_result.right_version})")
    print(f"Total Differences: {diff_result.difference_count}")
    print(f"Equal Semantics:   {diff_result.semantically_equal}\n")

    print("Discovered Semantic Differences")
    print("--------------------------------------------------------")
    for diff in diff_result.differences:
        sev_tag = f"[{diff.severity.value:<8}]"
        type_tag = f"{diff.difference_type.value:<22}"
        print(f"{sev_tag} {type_tag} Path: {diff.semantic_path}")
        print(f"            Canonical: {diff.left_value} -> Defective: {diff.right_value}")
        print(f"            {diff.description}\n")

    # 2. Impact Analysis
    analyzer = ImpactAnalyzer()
    impact = analyzer.analyze(diff_result, canonical_pkg)

    print("Impact & Blast Radius Analysis")
    print("--------------------------------------------------------")
    print(f"Changed Nodes:       {', '.join(impact.changed_nodes)}")
    print(f"Directly Affected:   {', '.join(impact.directly_affected_nodes)}")
    print(f"Downstream Affected: {', '.join(impact.downstream_affected_nodes)}")
    print(f"Affected Outputs:    {', '.join(impact.affected_outputs)}")
    print(f"Affected Coverages:  {', '.join(impact.affected_coverages)}\n")

    print("Candidate Risk Predicates (Exercising Differences)")
    print("--------------------------------------------------------")
    for pred in impact.candidate_risk_predicates:
        print(f"Predicate ID: {pred.id}")
        if pred.temporal_start or pred.temporal_end:
            print(f"  Temporal Window: {pred.temporal_start} to {pred.temporal_end}")
        for clause in pred.clauses:
            print(f"  Clause: {clause.field} {clause.operator.value} {clause.value}")
        print(f"  Description: {pred.description}\n")
    print("========================================================\n")


if __name__ == "__main__":
    main()

