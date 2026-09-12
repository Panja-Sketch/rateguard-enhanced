import logging
import os
import time
from decimal import Decimal

from app.engines.diff import compare_packages
from app.engines.impact import ImpactAnalyzer
from app.engines.portfolio.models import DefectExposure, PortfolioExposureResult, SyntheticPolicy
from app.engines.portfolio.predicate_evaluator import matches_predicate
from app.engines.portfolio.repricing import reprice_policy
from app.ipir.package import IPIRPackage

logger = logging.getLogger(__name__)


def _get_mem_mb() -> float:
    try:
        import psutil
        proc = psutil.Process(os.getpid())
        return round(proc.memory_info().rss / (1024 * 1024), 2)
    except Exception:
        return 0.0


class PortfolioAnalyzer:
    """Analyzes synthetic portfolios for blast radius, trace divergence, and exposure."""

    def analyze(
        self,
        policies: list[SyntheticPolicy],
        canonical_pkg: IPIRPackage,
        target_pkg: IPIRPackage,
    ) -> PortfolioExposureResult:
        """Executes portfolio-wide impact analysis, repricing, and financial exposure."""
        start_time = time.perf_counter()
        start_mem = _get_mem_mb()
        logger.info("Portfolio evaluation started for %d policies. Starting process memory: %.2f MB", len(policies), start_mem)

        diff_result = compare_packages(canonical_pkg, target_pkg)
        impact = ImpactAnalyzer().analyze(diff_result, canonical_pkg)

        total_policies = len(policies)
        exposed_policy_map: dict[str, set[str]] = {}  # policy_id -> set of issue_ids
        issue_predicates = impact.candidate_risk_predicates

        # 1. Evaluate Predicates against Portfolio
        for pol in policies:
            for pred in issue_predicates:
                if matches_predicate(pol, pred):
                    if pol.policy_id not in exposed_policy_map:
                        exposed_policy_map[pol.policy_id] = set()
                    exposed_policy_map[pol.policy_id].add(pred.id)

        exposed_policy_ids = set(exposed_policy_map.keys())
        exposed_count = len(exposed_policy_ids)
        exposed_pct = round((exposed_count / total_policies * 100.0) if total_policies else 0.0, 2)

        # 2. Reprice Exposed Policies
        repriced_count = 0
        behavioral_affected_ids: set[str] = set()
        financial_affected_ids: set[str] = set()
        policy_issue_matches: dict[str, set[str]] = {}

        total_expected_prem = Decimal("0.00")
        total_target_prem = Decimal("0.00")
        total_abs_var = Decimal("0.00")
        total_signed_var = Decimal("0.00")

        undercharged_count = 0
        total_undercharge = Decimal("0.00")
        overcharged_count = 0
        total_overcharge = Decimal("0.00")
        max_single_var = Decimal("0.00")

        issue_stats: dict[str, dict] = {
            pred.id: {
                "name": pred.description,
                "exposed_count": 0,
                "behavioral_count": 0,
                "financial_count": 0,
                "expected_prem": Decimal("0.00"),
                "target_prem": Decimal("0.00"),
                "abs_var": Decimal("0.00"),
                "signed_var": Decimal("0.00"),
            }
            for pred in issue_predicates
        }

        pol_dict = {p.policy_id: p for p in policies}

        for pid in exposed_policy_ids:
            pol = pol_dict[pid]
            recon = reprice_policy(pol, canonical_pkg, target_pkg, diff_result)
            repriced_count += 1

            matched_preds = exposed_policy_map[pid]
            for pred_id in matched_preds:
                issue_stats[pred_id]["exposed_count"] += 1

            if recon.trace_diverged:
                behavioral_affected_ids.add(pid)
                for pred_id in matched_preds:
                    issue_stats[pred_id]["behavioral_count"] += 1

            if not recon.premium_matches:
                financial_affected_ids.add(pid)
                total_expected_prem += recon.expected_premium
                total_target_prem += recon.actual_premium
                var = recon.actual_premium - recon.expected_premium
                abs_v = abs(var)

                total_signed_var += var
                total_abs_var += abs_v

                if abs_v > max_single_var:
                    max_single_var = abs_v

                if var < Decimal("0"):
                    undercharged_count += 1
                    total_undercharge += abs(var)
                elif var > Decimal("0"):
                    overcharged_count += 1
                    total_overcharge += var

                for pred_id in matched_preds:
                    issue_stats[pred_id]["financial_count"] += 1
                    issue_stats[pred_id]["expected_prem"] += recon.expected_premium
                    issue_stats[pred_id]["target_prem"] += recon.actual_premium
                    issue_stats[pred_id]["abs_var"] += abs_v
                    issue_stats[pred_id]["signed_var"] += var

                policy_issue_matches[pid] = matched_preds

        fin_count = len(financial_affected_ids)
        beh_count = len(behavioral_affected_ids)

        beh_pct = round((beh_count / total_policies * 100.0) if total_policies else 0.0, 2)
        fin_pct = round((fin_count / total_policies * 100.0) if total_policies else 0.0, 2)

        avg_var = (
            round(total_abs_var / Decimal(str(fin_count)), 2) if fin_count > 0 else Decimal("0.00")
        )

        # Multi-defect policy count (policies matching multiple issue predicates)
        multi_defect_count = sum(1 for pid, issues in exposed_policy_map.items() if len(issues) > 1)

        # Build issue breakdown list
        issue_breakdown = [
            DefectExposure(
                issue_id=p_id,
                issue_name=st["name"],
                exposed_count=st["exposed_count"],
                behaviorally_affected_count=st["behavioral_count"],
                financially_affected_count=st["financial_count"],
                total_expected_premium=st["expected_prem"],
                total_target_premium=st["target_prem"],
                signed_variance=st["signed_var"],
                absolute_variance=st["abs_var"],
                exposed_policy_pct=round(
                    (st["exposed_count"] / total_policies * 100.0) if total_policies else 0.0, 2
                ),
                affected_policy_pct=round(
                    (st["financial_count"] / total_policies * 100.0) if total_policies else 0.0, 2
                ),
            )
            for p_id, st in issue_stats.items()
        ]

        elapsed_sec = time.perf_counter() - start_time
        pol_per_sec = round(total_policies / elapsed_sec, 2) if elapsed_sec > 0 else 0.0
        end_mem = _get_mem_mb()
        logger.info(
            "Portfolio evaluation finished in %.2fs (%.2f pol/sec). Ending process memory: %.2f MB (delta: %+.2f MB).",
            elapsed_sec,
            pol_per_sec,
            end_mem,
            end_mem - start_mem,
        )

        return PortfolioExposureResult(
            portfolio_id="AZ_HO3_2026_SYNTHETIC_50K",
            total_policies=total_policies,
            exposed_policy_count=exposed_count,
            exposed_policy_pct=exposed_pct,
            behaviorally_affected_count=beh_count,
            behaviorally_affected_pct=beh_pct,
            financially_affected_count=fin_count,
            financially_affected_pct=fin_pct,
            total_expected_premium=total_expected_prem,
            total_target_premium=total_target_prem,
            total_signed_variance=total_signed_var,
            total_absolute_variance=total_abs_var,
            undercharged_policy_count=undercharged_count,
            total_undercharge_amount=total_undercharge,
            overcharged_policy_count=overcharged_count,
            total_overcharge_amount=total_overcharge,
            average_variance_per_affected_policy=avg_var,
            max_single_policy_variance=max_single_var,
            multi_defect_policy_count=multi_defect_count,
            defect_exposures=issue_breakdown,
            issue_breakdown=issue_breakdown,
            performance_telemetry={
                "elapsed_seconds": round(elapsed_sec, 3),
                "policies_per_second": pol_per_sec,
                "policies_repriced": repriced_count,
            },
        )
