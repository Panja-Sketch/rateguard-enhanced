"""Tests for the candidate/staging deployment workflow:

- deploy_candidate.sh's default dry-run mode never invokes gcloud and always
  reports isolated staging resource names (not production ones);
- verify_candidate.py / test_dlq_poison_delivery.py refuse to run without
  their explicit opt-in flags, with zero network/subprocess calls before
  that refusal.

None of these tests execute any gcloud command or touch a real deployment.
"""

import subprocess
import sys
from pathlib import Path

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


def _run_bash_script(relative_path: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH_EXECUTABLE, relative_path, *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_deploy_candidate_default_mode_prints_plan_without_gcloud() -> None:
    result = _run_bash_script("infrastructure/deploy_candidate.sh")
    assert result.returncode == 0
    assert "no gcloud command below has been executed" in result.stdout
    # The plan must never claim gcloud was actually invoked.
    assert "CANDIDATE DEPLOYMENT COMPLETE" not in result.stdout


def test_deploy_candidate_plan_uses_isolated_staging_resource_names() -> None:
    result = _run_bash_script("infrastructure/deploy_candidate.sh")
    for expected in (
        "assurance-runs-staging",
        "assurance-worker-staging",
        "assurance-runs-staging-dlq",
        "assurance-runs-staging-dlq-inspect",
        "assurance_runs_staging",
        "rateguard_staging",
        "rateguard-ai-artifacts-staging",
    ):
        assert expected in result.stdout, f"missing isolated staging resource name: {expected}"


def test_deploy_candidate_plan_never_targets_production_bigquery_or_bucket() -> None:
    """The production BigQuery dataset name and artifact bucket name must
    only ever appear inside an explicit "NOT done" disclaimer, never as
    something --deploy-candidate would actually write to."""
    result = _run_bash_script("infrastructure/deploy_candidate.sh")
    assert "No write to the production BigQuery dataset" in result.stdout
    assert "rateguard-ai-artifacts-staging" in result.stdout
    assert "RATEGUARD_BIGQUERY_DATASET=rateguard_staging" in result.stdout
    assert "RATEGUARD_GCS_BUCKET=rateguard-ai-artifacts-staging" in result.stdout


def test_deploy_candidate_plan_loads_only_synthetic_demonstration_portfolio() -> None:
    result = _run_bash_script("infrastructure/deploy_candidate.sh")
    assert "synthetic demonstration portfolio" in result.stdout
    assert "upload_synthetic_portfolio_bigquery.py" in result.stdout
    assert "setup_bigquery.py" in result.stdout


def test_deploy_candidate_plan_never_names_production_topic_as_the_target() -> None:
    """The bare production topic/subscription names must never appear as
    something this script would publish to or subscribe from — only as part
    of the isolated staging names (which contain them as substrings, e.g.
    'assurance-runs' is a substring of 'assurance-runs-staging') or explicit
    "NOT done" disclaimers."""
    result = _run_bash_script("infrastructure/deploy_candidate.sh")
    assert "NOT done by this script, ever" in result.stdout
    assert "no production traffic change" in result.stdout.lower()
    assert "no api key" in result.stdout.lower()


def test_deploy_candidate_immutable_image_tag_is_not_latest() -> None:
    result = _run_bash_script("infrastructure/deploy_candidate.sh")
    assert "Immutable image tag:" in result.stdout
    line = next(line for line in result.stdout.splitlines() if line.startswith("Immutable image tag:"))
    tag = line.split(":", 1)[1].strip()
    assert tag != "latest"
    assert tag.startswith("candidate-")


def test_deploy_candidate_plan_adds_candidate_web_origin_to_cors_without_dropping_production() -> None:
    """Regression: the candidate rateguard-web origin (a Cloud Run
    traffic-tag URL) must be added to RATEGUARD_CORS_ORIGINS alongside --
    never instead of -- the production origin already declared in
    infrastructure/runtime-env.yaml. Missing this meant every browser
    request from the candidate frontend was silently blocked by
    CORSMiddleware even though a plain CLI/urllib client saw no failure."""
    result = _run_bash_script("infrastructure/deploy_candidate.sh")
    line = next(line for line in result.stdout.splitlines() if line.strip().startswith("RATEGUARD_CORS_ORIGINS="))
    assert "https://rateguard-web-iqofutwtva-uc.a.run.app" in line
    assert "https://candidate---rateguard-web-iqofutwtva-uc.a.run.app" in line


def test_promote_candidate_refuses_without_digest() -> None:
    result = _run_bash_script("infrastructure/promote_candidate.sh")
    assert result.returncode == 2
    assert "image-digest" in result.stdout


def test_promote_candidate_dry_run_does_not_execute_traffic_shift() -> None:
    result = _run_bash_script("infrastructure/promote_candidate.sh", "--image-digest=sha256:testonly")
    assert result.returncode == 0
    assert "PLAN" in result.stdout
    assert "update-traffic" in result.stdout  # documented, not executed


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
