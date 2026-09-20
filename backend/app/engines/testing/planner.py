from collections.abc import Sequence

from app.engines.diff.models import SemanticDiffResult
from app.engines.impact.models import ImpactAnalysis
from app.engines.testing.candidate_generator import generate_candidate_scenarios
from app.engines.testing.models import PricingTestPlan, PricingTestScenario
from app.engines.testing.optimizer import optimize_test_plan
from app.engines.testing.package_probes import (
    DEFAULT_MAX_PROBES,
    canonical_probe_key,
    generate_package_probes,
)
from app.ipir.package import IPIRPackage
from app.ipir.v0_2.control_cases import ControlCase


class PricingTestPlanner:
    """Generates risk-directed pricing test plans deterministically.

    The plan combines, in this priority order (each probe carries its origin in
    ``metadata["probe_origin"]``):

    1. valid workbook control cases (``CONTROL_CASE``);
    2. a declared-defaults baseline when no control case exists (``BASELINE``);
    3. boundary probes mined from the compiled IPIR's tables and rule
       conditions (``BOUNDARY``);
    4. mutation-targeted probes for known structural differences
       (``MUTATION``), selected by the optimizer into the remaining slots.

    Equivalent probes are removed and the ordering is deterministic. When no
    executable probe can be built the plan is empty and ``planning_metadata``
    explains why, so callers can require review instead of passing.
    """

    def generate_plan(
        self,
        package: IPIRPackage,
        diff_result: SemanticDiffResult,
        impact: ImpactAnalysis,
        control_cases: Sequence[ControlCase] = (),
        max_scenarios: int = DEFAULT_MAX_PROBES,
    ) -> PricingTestPlan:
        probes = generate_package_probes(package, control_cases, limit=max_scenarios)
        # Mutation-targeted candidates are only meaningful against a valid base
        # policy. With none (no verified control case and no declared defaults)
        # the generator must fail closed rather than fabricate an all-zero
        # policy, so it contributes nothing and the plan is reported empty.
        mutation_candidates = (
            generate_candidate_scenarios(
                diff_result, impact, package, base_risk=probes.base_risk, base_date=probes.base_date
            )
            if probes.base_risk is not None
            else []
        )

        seen = {
            canonical_probe_key(p.risk_values, p.effective_date, p.transaction_type) for p in probes.scenarios
        }
        unique_mutations: list[PricingTestScenario] = []
        for cand in mutation_candidates:
            key = canonical_probe_key(cand.risk_values, cand.effective_date, cand.transaction_type)
            if key in seen:
                continue
            seen.add(key)
            unique_mutations.append(cand)

        # Slot budget: control cases and the baseline always run; when
        # structural differences produced mutation-targeted probes, they get
        # up to half of the remaining slots (never starved by boundaries), and
        # boundary probes fill the rest.
        core = [p for p in probes.scenarios if p.metadata["probe_origin"] in ("CONTROL_CASE", "BASELINE")]
        boundary = [p for p in probes.scenarios if p.metadata["probe_origin"] == "BOUNDARY"]
        slots = max(0, max_scenarios - len(core))
        mutation_quota = min(len(unique_mutations), max(1, slots // 2)) if unique_mutations and slots else 0
        chosen_mutations = (
            optimize_test_plan(unique_mutations, max_scenarios=mutation_quota) if mutation_quota else []
        )
        chosen_boundary = boundary[: max(0, slots - len(chosen_mutations))]
        selected = core + chosen_boundary + chosen_mutations
        for idx, sc in enumerate(selected, start=1):
            sc.id = f"RG-{idx:03d}"

        candidates = list(probes.scenarios) + unique_mutations
        diff_ids = [d.id for d in diff_result.differences]
        covered: set[str] = set()
        for sc in selected:
            covered.update(sc.target_difference_ids)

        diff_coverage_pct = (len(covered) / len(diff_ids) * 100.0) if diff_ids else 100.0
        candidate_reduction_pct = (
            ((len(candidates) - len(selected)) / len(candidates) * 100.0) if candidates else 0.0
        )

        origin_counts: dict[str, int] = {}
        for sc in selected:
            origin = str(sc.metadata.get("probe_origin", "UNKNOWN"))
            origin_counts[origin] = origin_counts.get(origin, 0) + 1

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
                "differences_covered": len(covered),
                "total_differences": len(diff_ids),
                "probe_origin_counts": origin_counts,
            },
            planning_metadata={
                "planner": "RateGuard Risk-Directed Test Planner 0.2",
                "control_cases_supplied": len(control_cases),
                "skipped_probes": probes.skipped,
                "no_executable_probe": not selected,
            },
        )
