"""Opt-in full live RELEASE_CONFORMANCE mission smoke test.

Runs the REAL `AssuranceSupervisor` against the real deterministic engines,
the existing sample IPIR packages (AZ_HO3_2026_09 vs AZ_HO3_2026_09_DEFECTIVE),
and the existing local 50K-row synthetic portfolio CSV — entirely in-process.
This is exactly the same code path as
`tests/unit/test_missions_v2.py::test_supervisor_defective_conformance_and_remediation`,
just constructed with a real (not fake/disabled) `GeminiDecisionClient` so its
bounded, structured decisions can actually be consulted wherever the
deterministic policy allows it. No second implementation — same supervisor,
same engines, same demo packages.

Explicitly does NOT touch:
  - Firestore  (uses `InMemoryRunStore`)
  - Pub/Sub    (calls `AssuranceSupervisor.run_mission` directly, no worker/queue)
  - GCS        (demo IPIR packages are loaded from local JSON via
                `resolve_demo_package`, not the artifact store)
  - deployment (nothing here invokes gcloud or any deploy tooling)

Usage (requires explicit opt-in — the script refuses to run without it):

    GOOGLE_GENAI_USE_VERTEXAI=true GOOGLE_CLOUD_PROJECT=rateguard-ai \\
        GOOGLE_CLOUD_LOCATION=global python scripts/live_mission_smoke.py --yes-live-call

Prints a summary covering every item the full-mission verification requires:
Gemini decisions with real invocation/response ids, that no model_id is
attached to a deterministic action, semantic diff / experiment / portfolio /
remediation counts, and the final release decision with its blocking reasons.
"""

import argparse
import sys

from app.agents.config import get_agent_config
from app.agents.gemini_client import AUTH_MODE_NONE, GeminiDecisionClient
from app.agents.supervisor import AssuranceSupervisor
from app.api.assurance import resolve_demo_package
from app.models.mission import AssuranceMission, ComparisonMode, MissionObjective, PricingSourceRef
from app.storage.memory_store import InMemoryRunStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--yes-live-call",
        action="store_true",
        help="Required explicit opt-in. Without this flag the script refuses to run.",
    )
    args = parser.parse_args()

    if not args.yes_live_call:
        print("Refusing to run: pass --yes-live-call to explicitly opt into a live mission.")
        return 2

    config = get_agent_config()
    probe_client = GeminiDecisionClient(config)
    auth_mode, _ = probe_client._resolve_auth_mode()

    if not config.agent_enabled:
        print("Refusing to run: RATEGUARD_AGENT_ENABLED is false (Gemini disabled).")
        return 2
    if auth_mode == AUTH_MODE_NONE:
        print(
            "Refusing to run: no live credentials configured. Set "
            "GOOGLE_GENAI_USE_VERTEXAI=true with GOOGLE_CLOUD_PROJECT/GOOGLE_CLOUD_LOCATION "
            "(and Application Default Credentials), or an API key."
        )
        return 2

    print(f"Running live RELEASE_CONFORMANCE mission (model={config.gemini_model}, auth_mode={auth_mode})")
    print("Storage: InMemoryRunStore only. No Firestore, Pub/Sub, GCS, or deployment involved.")

    store = InMemoryRunStore()
    supervisor = AssuranceSupervisor(store)  # real GeminiDecisionClient built from live config

    left_pkg = resolve_demo_package("AZ_HO3_2026_09")
    right_pkg = resolve_demo_package("AZ_HO3_2026_09_DEFECTIVE")
    mission = AssuranceMission(
        mission_id="MIS-LIVE-SMOKE-01",
        name="Live Vertex AI Mission Smoke Test",
        mode=ComparisonMode.RELEASE_CONFORMANCE,
        objective=MissionObjective(product="AZ_HO3", jurisdiction="Arizona", effective_period_start="2026-09-01"),
        source_a=PricingSourceRef(source_id="AZ_HO3_2026_09", source_type="SAMPLE_RELEASE", name="Intent"),
        source_b=PricingSourceRef(
            source_id="AZ_HO3_2026_09_DEFECTIVE", source_type="SAMPLE_RELEASE", name="Defective Target"
        ),
    )

    result = supervisor.run_mission(mission, left_pkg, right_pkg)

    print("\n--- Live Mission Result ---")
    print(f"overall_status:            {result.overall_status}")
    print(f"ai_runtime.model_status:   {result.ai_runtime.get('model_status')}")
    print(f"ai_runtime.gemini_calls_made: {result.ai_runtime.get('gemini_calls_made')}")
    print(f"semantic_analysis.status:  {result.semantic_analysis.status}")
    if result.semantic_analysis.data:
        print(f"  difference_count:        {result.semantic_analysis.data.difference_count}")
    print(f"experiments.status:        {result.experiments.status}")
    if result.experiments.data:
        print(
            f"  total_executed/mismatch: {result.experiments.data.total_executed}/"
            f"{result.experiments.data.mismatch_count}"
        )
    print(f"reconciliation.status:     {result.reconciliation.status}")
    print(f"blast_radius.status:       {result.blast_radius.status}")
    if result.blast_radius.data:
        print(f"  financially_affected_count: {result.blast_radius.data.financially_affected_count}")
    print(f"remediation.status:        {result.remediation.status}")
    print(f"revalidation.status:       {result.revalidation.status}")
    if result.revalidation.data:
        print(f"  exposure_eliminated_pct:  {result.revalidation.data.exposure_eliminated_pct}")
        print(
            f"  targeted rerun/passed:    {result.revalidation.data.targeted_tests_rerun}/"
            f"{result.revalidation.data.targeted_tests_passed}"
        )
        print(
            f"  regression rerun/passed:  {result.revalidation.data.regression_tests_rerun}/"
            f"{result.revalidation.data.regression_tests_passed}"
        )
    print(f"release_decision:          {result.release_decision.data.status if result.release_decision.data else None}")
    if result.release_decision.data:
        print(f"  blocking_reasons: {result.release_decision.data.blocking_reasons}")

    print("\n--- Agent Action Timeline ---")
    gemini_call_count = 0
    fallback_count = 0
    bad_model_id_stamp = False
    for action in result.agent_execution.data or []:
        if action.is_gemini_decision:
            gemini_call_count += 1
            has_ids = bool(action.model_id) and bool(action.invocation_id)
            print(
                f"  [GEMINI]   {action.decision_type:<28} model_id_present={bool(action.model_id)} "
                f"invocation_id_present={bool(action.invocation_id)} valid_ids={has_ids}"
            )
        elif action.is_fallback:
            fallback_count += 1
            print(f"  [FALLBACK] {action.decision_type or action.summary[:40]:<28} reason={action.fallback_reason}")
        else:
            if action.model_id is not None:
                bad_model_id_stamp = True
            print(f"  [DETERMINISTIC] {action.action_type:<16} {action.summary[:70]}")

    print(f"\nreal Gemini decisions applied: {gemini_call_count}")
    print(f"deterministic fallbacks used:  {fallback_count}")
    print(f"a deterministic action incorrectly carries a model_id: {bad_model_id_stamp}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
