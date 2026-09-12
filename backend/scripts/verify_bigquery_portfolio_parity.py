import argparse
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

# Ensure backend root is in sys.path when script is executed directly
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.engines.portfolio.analyzer import PortfolioAnalyzer  # noqa: E402
from app.engines.portfolio.models import PortfolioExposureResult  # noqa: E402
from app.ipir.package import IPIRPackage  # noqa: E402
from app.storage.portfolio.bigquery_repository import BigQueryPortfolioRepository  # noqa: E402
from app.storage.portfolio.local_repository import LocalPortfolioRepository  # noqa: E402


def compare_metrics(res_local: PortfolioExposureResult, res_bq: PortfolioExposureResult) -> list[tuple[str, Any, Any, bool]]:
    """Compares individual exposure metrics between local and BigQuery execution results using exact equality."""
    metrics = [
        ("Total Policies", res_local.total_policies, res_bq.total_policies),
        ("Exposed Policies", res_local.exposed_policy_count, res_bq.exposed_policy_count),
        ("Behaviorally Affected", res_local.behaviorally_affected_count, res_bq.behaviorally_affected_count),
        ("Financially Affected", res_local.financially_affected_count, res_bq.financially_affected_count),
        ("Expected Premium ($)", res_local.total_expected_premium, res_bq.total_expected_premium),
        ("Target Premium ($)", res_local.total_target_premium, res_bq.total_target_premium),
        ("Signed Variance ($)", res_local.total_signed_variance, res_bq.total_signed_variance),
        ("Absolute Variance ($)", res_local.total_absolute_variance, res_bq.total_absolute_variance),
        ("Undercharged Count", res_local.undercharged_count, res_bq.undercharged_count),
        ("Total Undercharge ($)", res_local.total_undercharge, res_bq.total_undercharge),
        ("Overcharged Count", res_local.overcharged_count, res_bq.overcharged_count),
        ("Total Overcharge ($)", res_local.total_overcharge, res_bq.total_overcharge),
    ]

    results = []
    for name, loc_val, bq_val in metrics:
        matches = loc_val == bq_val
        results.append((name, loc_val, bq_val, matches))

    return results


def verify_parity(limit: int | None = None) -> bool:
    """Verifies 100% financial and exposure metric parity between CSV and BigQuery repositories."""
    root_dir = Path(__file__).resolve().parent.parent.parent
    canonical_file = (
        root_dir / "data" / "implementations" / "canonical" / "AZ_HO3_2026_09_ipir.json"
    )
    defective_file = (
        root_dir / "data" / "implementations" / "defective" / "AZ_HO3_2026_09_ipir.json"
    )

    with open(canonical_file, encoding="utf-8") as f:
        canonical_pkg = IPIRPackage.model_validate_json(f.read())
    with open(defective_file, encoding="utf-8") as f:
        defective_pkg = IPIRPackage.model_validate_json(f.read())

    limit_desc = f"{limit:,} policies" if limit is not None else "Full 50,000 policies"
    print("========================================================")
    print("   RateGuard AI -- BigQuery & CSV Parity Verification   ")
    print(f"   Mode: {limit_desc}")
    print("========================================================\n")

    local_repo = LocalPortfolioRepository()
    bq_repo = BigQueryPortfolioRepository()

    print("1. Loading Local CSV Repository Policies...")
    local_pols = local_repo.load_policies(limit=limit)
    local_pols.sort(key=lambda p: p.policy_id)
    print(f"   Local Portfolio: {len(local_pols):,} policies loaded.")

    print("\n2. Loading BigQuery Repository Policies...")
    try:
        bq_pols = bq_repo.load_policies(limit=limit)
        bq_pols.sort(key=lambda p: p.policy_id)
        print(f"   BigQuery Portfolio: {len(bq_pols):,} policies loaded.")
    except Exception as e:
        print(f"   [ERROR] Failed to query BigQuery: {e}")
        return False

    if len(local_pols) != len(bq_pols):
        print(f"   [FAIL] Policy count mismatch: Local={len(local_pols):,} vs BQ={len(bq_pols):,}")
        return False

    analyzer = PortfolioAnalyzer()

    print("\n3. Executing Local Portfolio Analysis...")
    res_local = analyzer.analyze(local_pols, canonical_pkg, defective_pkg)

    print("4. Executing BigQuery Portfolio Analysis...")
    res_bq = analyzer.analyze(bq_pols, canonical_pkg, defective_pkg)

    print("\n-------------------------------------------------------------------------")
    print(f"{'Metric':<28} {'Local':<18} {'BigQuery':<18} {'Match':<8}")
    print("-------------------------------------------------------------------------")

    comparison_results = compare_metrics(res_local, res_bq)
    all_matched = True

    for name, loc_val, bq_val, matched in comparison_results:
        if isinstance(loc_val, Decimal):
            loc_str = f"{loc_val:,.2f}"
            bq_str = f"{bq_val:,.2f}"
        elif isinstance(loc_val, int):
            loc_str = f"{loc_val:,}"
            bq_str = f"{bq_val:,}"
        else:
            loc_str = str(loc_val)
            bq_str = str(bq_val)

        status_str = "PASS" if matched else "FAIL"
        if not matched:
            all_matched = False
        print(f"{name:<28} {loc_str:<18} {bq_str:<18} {status_str:<8}")

    print("-------------------------------------------------------------------------\n")

    if all_matched:
        print("PARITY: PASS (100% exact metric match)")
        print("========================================================\n")
        return True
    else:
        print("PARITY: FAIL (Discrepancy detected across repositories)")
        print("========================================================\n")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify BigQuery vs CSV portfolio analysis parity")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional policy limit for rapid smoke testing (defaults to full 50,000 portfolio)",
    )
    args = parser.parse_args()

    success = verify_parity(limit=args.limit)
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
