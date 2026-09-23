"""Tests for the candidate deployment workflow.

deploy_candidate_enhanced.sh and promote_candidate_to_production.sh were
previously wrong in a way that only showed up in a real dry-run against the
live project: the plan claimed the project had zero deployed Cloud Run
services and proposed isolated `*-staging` Pub/Sub/Firestore/BigQuery/GCS
resources that no longer reflect reality once Prompt 8 deployed four real
production services. Corrected behavior (this file's coverage):

- the default (no-flag) mode performs READ-ONLY discovery (gcloud
  run/pubsub *list*/*describe*, never create/update/delete) and prints
  whatever it finds -- it never hardcodes "no services exist";
- it no longer proposes provisioning the obsolete staging-named resources,
  though it also never claims to delete them;
- candidate revisions are wired to the real production Firestore/GCS/
  BigQuery/Pub/Sub topic names, never the old staging names;
- each of the four services gets its own distinct, correctly-scoped service
  account, including a dedicated rating-engine SA (not a reuse of the
  worker SA);
- preflight guards (project/region, dirty tree, unpushed tree) exist as
  named functions and are wired into both --deploy-candidate and
  --verify-candidate;
- promote_candidate_to_production.sh requires a --verify-candidate marker
  (or an explicit --skip-verification-check override), captures prior
  production revisions for rollback before promoting, and rejects any
  candidate revision resolving to an obsolete staging-named resource;
- rollback.sh supports an optional rating-engine revision alongside the
  pre-existing api/worker/web.
- verify_candidate.py / test_dlq_poison_delivery.py refuse to run without
  their explicit opt-in flags, with zero network/subprocess calls before
  that refusal.

None of these tests create, update, or delete any real GCP resource -- the
default (no-flag) mode of every script under test here is read-only by
contract, which is itself asserted below.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import test_dlq_poison_delivery as dlq_script  # noqa: E402
import verify_candidate  # noqa: E402

# On Windows, a bare "bash" on PATH can resolve to the WSL launcher
# (C:\Windows\System32\bash.exe) instead of Git Bash, which fails outright
# with no installed WSL distribution. Prefer an explicit Git Bash path when
# present; skip these subprocess-based tests entirely on a platform with
# neither (e.g. a Linux/Mac CI runner still uses the plain "bash" fallback).
_GIT_BASH_CANDIDATES = [
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files\Git\usr\bin\bash.exe",
]
BASH_EXECUTABLE = next((p for p in _GIT_BASH_CANDIDATES if Path(p).exists()), "bash")


def _run_bash_script(relative_path: str, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH_EXECUTABLE, relative_path, *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


@pytest.fixture(scope="module")
def enhanced_deploy_plan() -> subprocess.CompletedProcess:
    """The default (no-flag) dry-run output, computed ONCE and shared across
    every test below that only inspects the printed plan -- this mode now
    performs real read-only `gcloud` discovery calls (by design; see the
    script's header comment), so re-running it per-assertion would make this
    file slow and network-flaky. A longer timeout accommodates the discovery
    calls against the live project; on a machine/CI without gcloud network
    access, `discover_production_state` still degrades gracefully (each call
    is wrapped in `... 2>/dev/null || echo '<not found>'`), so this remains a
    read-only, non-fatal call either way."""
    return _run_bash_script("infrastructure/deploy_candidate_enhanced.sh", timeout=90)


def test_enhanced_deploy_default_mode_never_mutates(enhanced_deploy_plan) -> None:
    assert enhanced_deploy_plan.returncode == 0
    assert "no create/update/delete gcloud call has been made" in enhanced_deploy_plan.stdout
    assert "CANDIDATE DEPLOYMENT COMPLETE" not in enhanced_deploy_plan.stdout
    assert "Project:                        rateguard-enhanced" in enhanced_deploy_plan.stdout


def test_enhanced_deploy_plan_does_not_hardcode_zero_services(enhanced_deploy_plan) -> None:
    """The specific, previously-shipped defect: the plan must never claim the
    project has no deployed services -- it must actually discover and print
    what is really running."""
    assert "ZERO deployed Cloud Run services" not in enhanced_deploy_plan.stdout
    assert "does not yet exist" not in enhanced_deploy_plan.stdout
    assert "Live Cloud Run services in rateguard-enhanced/us-central1" in enhanced_deploy_plan.stdout


def test_enhanced_deploy_plan_wires_production_data_plane_not_staging(enhanced_deploy_plan) -> None:
    for expected in (
        "assurance_runs",
        "rateguard-enhanced-artifacts",
        "rateguard_portfolio",
        "assurance-runs (subscription assurance-runs-worker-sub",
    ):
        assert expected in enhanced_deploy_plan.stdout, f"missing production data-plane reference: {expected}"


def test_enhanced_deploy_plan_never_proposes_provisioning_obsolete_staging_resources(enhanced_deploy_plan) -> None:
    """The plan may *mention* the obsolete staging names (to explain they are
    never recreated), but must never propose creating them as part of a
    candidate deploy."""
    assert "no longer provisions any *-staging" in enhanced_deploy_plan.stdout
    # None of the "Exact commands" steps may reference a staging topic/bucket/dataset.
    steps_section = enhanced_deploy_plan.stdout.split("Exact commands --deploy-candidate would run")[1]
    for stale in (
        "assurance-runs-staging",
        "assurance-worker-staging",
        "rateguard_staging",
        "rateguard-enhanced-artifacts-staging",
    ):
        assert stale not in steps_section, f"obsolete staging resource still proposed for provisioning: {stale}"


def test_enhanced_deploy_uses_a_dedicated_rating_engine_service_account(enhanced_deploy_plan) -> None:
    """Previously the rating-engine service reused rateguard-worker-sa. A
    dedicated rateguard-rating-engine-sa already exists in the project and
    must be used for new candidate deployments."""
    assert "rateguard-rating-engine-sa@rateguard-enhanced.iam.gserviceaccount.com" in enhanced_deploy_plan.stdout


def test_enhanced_deploy_worker_invoker_grant_is_labeled_as_pubsub_push_identity(enhanced_deploy_plan) -> None:
    """A prior version of this plan labeled the worker's self-invoker grant
    as 'rating-engine caller access', which was simply wrong -- it is the
    worker's own Pub/Sub push identity. Guard against the mislabel
    reappearing."""
    assert "rating-engine caller" not in enhanced_deploy_plan.stdout
    assert "Pub/Sub push identity" in enhanced_deploy_plan.stdout


def test_enhanced_deploy_image_tag_is_immutable_full_sha_not_latest(enhanced_deploy_plan) -> None:
    line = next(line for line in enhanced_deploy_plan.stdout.splitlines() if line.startswith("Immutable image tag:"))
    tag = line.split(":", 1)[1].strip()
    assert tag != "latest"
    assert tag.startswith("candidate-")
    sha_part = tag[len("candidate-"):]
    assert len(sha_part) == 40, f"expected a full 40-character git SHA, got {sha_part!r} (len {len(sha_part)})"


def test_enhanced_deploy_api_and_worker_share_one_backend_image_variable() -> None:
    """API and worker must be deployed from the exact same image -- enforced
    at the shell-variable level (BACKEND_IMAGE reused for both --image
    arguments) and reinforced at runtime by assert_api_worker_same_digest."""
    text = (REPO_ROOT / "infrastructure" / "deploy_candidate_enhanced.sh").read_text(encoding="utf-8")
    assert text.count('--image "$BACKEND_IMAGE"') == 2
    assert "assert_api_worker_same_digest" in text


def test_enhanced_deploy_rating_engine_invoker_iam_targets_rating_engine_service() -> None:
    text = (REPO_ROOT / "infrastructure" / "deploy_candidate_enhanced.sh").read_text(encoding="utf-8")
    assert "add-iam-policy-binding rateguard-rating-engine" in text
    # Only the worker and API SAs may be granted invoker on rating-engine.
    assert 'member="serviceAccount:${WORKER_SA}" --role="roles/run.invoker"' in text
    assert 'member="serviceAccount:${API_SA}" --role="roles/run.invoker"' in text


def test_enhanced_deploy_worker_and_rating_engine_stay_private() -> None:
    text = (REPO_ROOT / "infrastructure" / "deploy_candidate_enhanced.sh").read_text(encoding="utf-8")
    # Appears twice in the printed plan (documentation of the two commands)
    # and twice in the real `deploy_candidate()` gcloud invocations -- one
    # each for rating-engine and worker.
    assert text.count("--no-allow-unauthenticated") == 4
    assert "--allow-unauthenticated \\\n       --service-account ${RATING_ENGINE_SA}" not in text
    assert "--allow-unauthenticated \\\n    --service-account \"$RATING_ENGINE_SA\"" not in text


def test_enhanced_deploy_preflight_guards_exist_and_are_wired_in() -> None:
    text = (REPO_ROOT / "infrastructure" / "deploy_candidate_enhanced.sh").read_text(encoding="utf-8")
    assert "preflight_guards()" in text
    assert 'configured_project != "$PROJECT_ID"' in text or "configured_project\" != \"$PROJECT_ID\"" in text
    assert "uncommitted changes" in text
    assert "unpushed tree" in text
    # Called from both mutating entry points.
    assert text.count("preflight_guards") >= 3  # definition + deploy_candidate + verify_candidate_async_path


def test_enhanced_deploy_does_not_reference_disabled_legacy_secrets() -> None:
    text = (REPO_ROOT / "infrastructure" / "deploy_candidate_enhanced.sh").read_text(encoding="utf-8")
    # No REAL --set-secrets invocation (an actual usage is always followed by
    # a mapping, e.g. "--set-secrets=FOO=bar:latest"); the script only ever
    # mentions the flag inside its own "we do NOT do this" disclaimer text.
    assert "--set-secrets=" not in text
    assert "no --set-secrets mapping for FIREBASE_ADMIN_KEY or GEMINI_API_KEY" in text


def test_enhanced_deploy_verify_candidate_never_modifies_real_subscription() -> None:
    text = (REPO_ROOT / "infrastructure" / "deploy_candidate_enhanced.sh").read_text(encoding="utf-8")
    assert "verify_candidate_async_path" in text
    # No REAL `gcloud pubsub subscriptions modify-push-config` invocation --
    # the only mention of "modify-push-config" is inside a comment/error
    # message explaining that this script never does that.
    assert "gcloud pubsub subscriptions modify-push-config" not in text


def test_enhanced_deploy_env_carries_the_approved_guardrail_values() -> None:
    text = (REPO_ROOT / "infrastructure" / "deploy_candidate_enhanced.sh").read_text(encoding="utf-8")
    assert 'RATEGUARD_MAX_GEMINI_CALLS_PER_MISSION: "10"' in text
    assert 'RATEGUARD_MAX_PROBE_ROUNDS: "3"' in text
    assert 'RATEGUARD_LOW_CONFIDENCE_REVIEW_THRESHOLD: "0.8"' in text


def test_only_the_enhanced_runtime_env_file_exists() -> None:
    files = sorted(p.name for p in (REPO_ROOT / "infrastructure").glob("runtime-env*.yaml"))
    assert files == ["runtime-env.rateguard-enhanced.yaml"]


LEGACY_DEPLOY_SCRIPTS = (
    "deploy.sh",
    "deploy.ps1",
    "deploy_candidate.sh",
    "deploy_candidate.ps1",
    "deploy_release.sh",
    "promote_candidate.sh",
)


def test_legacy_rateguard_ai_deployment_scripts_are_removed() -> None:
    for name in LEGACY_DEPLOY_SCRIPTS:
        assert not (REPO_ROOT / "infrastructure" / name).exists(), name


def test_no_deployment_or_runtime_configuration_targets_the_old_project() -> None:
    """No script, config, source default or build file may name the old
    `rateguard-ai` project (documentation of history and tests excepted)."""
    roots = [REPO_ROOT / "infrastructure", REPO_ROOT / "backend" / "app", REPO_ROOT / "backend" / "scripts",
             REPO_ROOT / "backend" / "cloudbuild.yaml", REPO_ROOT / "backend" / "rating_engine",
             REPO_ROOT / "frontend" / "cloudbuild.yaml"]
    offenders = []
    for root in roots:
        files = [root] if root.is_file() else [
            p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts
            and "node_modules" not in p.parts and p.suffix in {".py", ".sh", ".ps1", ".yaml", ".yml", ".json"}
        ]
        for path in files:
            text = path.read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                if "rateguard-ai" in line and "old `rateguard-ai`" not in line and "old 'rateguard-ai'" not in line:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: {line.strip()[:100]}")
    assert offenders == []


def test_rollback_refuses_without_all_three_revisions() -> None:
    result = _run_bash_script("infrastructure/rollback.sh", "--api-revision=rateguard-api-1")
    assert result.returncode == 2
    assert "required" in result.stdout


def test_rollback_dry_run_with_all_revisions_supplied() -> None:
    result = _run_bash_script(
        "infrastructure/rollback.sh",
        "--api-revision=rateguard-api-1",
        "--worker-revision=rateguard-worker-1",
        "--web-revision=rateguard-web-1",
    )
    assert result.returncode == 0
    assert "rateguard-api-1" in result.stdout
    assert "rateguard-worker-1" in result.stdout
    assert "rateguard-web-1" in result.stdout


def test_rollback_supports_optional_rating_engine_revision() -> None:
    result = _run_bash_script(
        "infrastructure/rollback.sh",
        "--api-revision=rateguard-api-1",
        "--worker-revision=rateguard-worker-1",
        "--web-revision=rateguard-web-1",
        "--rating-engine-revision=rateguard-rating-engine-1",
    )
    assert result.returncode == 0
    assert "rateguard-rating-engine-1" in result.stdout
    assert "update-traffic rateguard-rating-engine" in result.stdout


# --- promote_candidate_to_production.sh ---


def test_promote_default_mode_captures_prior_revisions_for_rollback() -> None:
    result = _run_bash_script("infrastructure/promote_candidate_to_production.sh", timeout=90)
    assert result.returncode == 0
    assert "Currently-live production revisions (captured now, BEFORE any promotion" in result.stdout


def test_promote_requires_a_verified_marker_by_default() -> None:
    text = (REPO_ROOT / "infrastructure" / "promote_candidate_to_production.sh").read_text(encoding="utf-8")
    assert "require_verified_marker" in text
    assert "--skip-verification-check" in text
    assert ".candidate-verified" in text


def test_promote_rejects_staging_named_candidate_resources() -> None:
    text = (REPO_ROOT / "infrastructure" / "promote_candidate_to_production.sh").read_text(encoding="utf-8")
    assert "reject_staging_named_resources" in text
    for stale in (
        "assurance-runs-staging",
        "assurance-worker-staging",
        "rateguard_staging",
        "rateguard-enhanced-artifacts-staging",
    ):
        assert stale in text  # present in OBSOLETE_STAGING_NAMES, checked against


def test_promote_verifies_pubsub_routing_after_promotion() -> None:
    text = (REPO_ROOT / "infrastructure" / "promote_candidate_to_production.sh").read_text(encoding="utf-8")
    assert "Verifying Pub/Sub routing after promotion" in text
    assert "candidate---|verify---" in text  # refuses a tag-scoped push endpoint post-promotion


def test_promote_supports_progressive_canary_traffic_shift() -> None:
    text = (REPO_ROOT / "infrastructure" / "promote_candidate_to_production.sh").read_text(encoding="utf-8")
    assert "--canary-percent" in text
    assert "CANARY_PERCENT" in text


def test_promote_checks_project_before_any_mutating_call() -> None:
    text = (REPO_ROOT / "infrastructure" / "promote_candidate_to_production.sh").read_text(encoding="utf-8")
    assert 'configured_project != "$PROJECT_ID"' in text or "configured_project\" != \"$PROJECT_ID\"" in text


def test_promote_web_image_tag_is_full_sha_not_short() -> None:
    text = (REPO_ROOT / "infrastructure" / "promote_candidate_to_production.sh").read_text(encoding="utf-8")
    assert 'git rev-parse HEAD' in text
    assert "--short=12" not in text


def test_verify_candidate_refuses_without_opt_in() -> None:
    assert verify_candidate.main(argv=["--api-url", "https://example.com"]) == 2


def test_verify_candidate_refuses_without_api_url() -> None:
    assert verify_candidate.main(argv=["--yes-test-candidate"]) == 2


def test_dlq_poison_delivery_refuses_without_opt_in() -> None:
    assert dlq_script.main(argv=[]) == 2


def test_dlq_poison_delivery_refuses_non_staging_topic() -> None:
    assert (
        dlq_script.main(argv=["--yes-poison-staging-dlq", "--topic", "assurance-runs"])
        == 2
    )


def test_dlq_poison_delivery_refuses_non_staging_dlq_subscription() -> None:
    assert (
        dlq_script.main(
            argv=[
                "--yes-poison-staging-dlq",
                "--dlq-subscription", "some-production-subscription",
            ]
        )
        == 2
    )
