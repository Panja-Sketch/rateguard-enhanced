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


def test_enhanced_deploy_default_mode_prints_plan_without_gcloud() -> None:
    result = _run_bash_script("infrastructure/deploy_candidate_enhanced.sh")
    assert result.returncode == 0
    assert "no gcloud command below has been executed" in result.stdout
    assert "CANDIDATE DEPLOYMENT COMPLETE" not in result.stdout
    assert "Project:                        rateguard-enhanced" in result.stdout


def test_enhanced_deploy_plan_uses_isolated_staging_resource_names() -> None:
    result = _run_bash_script("infrastructure/deploy_candidate_enhanced.sh")
    for expected in (
        "assurance-runs-staging",
        "assurance-worker-staging",
        "assurance-runs-staging-dlq",
        "assurance-runs-staging-dlq-inspect",
        "assurance_runs_staging",
        "rateguard_staging",
        "rateguard-enhanced-artifacts-staging",
    ):
        assert expected in result.stdout, f"missing isolated staging resource name: {expected}"


def test_enhanced_deploy_image_tag_is_immutable_not_latest() -> None:
    result = _run_bash_script("infrastructure/deploy_candidate_enhanced.sh")
    line = next(line for line in result.stdout.splitlines() if line.startswith("Immutable image tag:"))
    tag = line.split(":", 1)[1].strip()
    assert tag != "latest" and tag.startswith("candidate-")


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
