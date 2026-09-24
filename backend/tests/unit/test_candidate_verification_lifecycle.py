"""Regression tests for the candidate-verification lifecycle in
infrastructure/deploy_candidate_enhanced.sh (--prepare-verification,
--complete-verification, --abort-verification, --record-verified).

The first real --verify-candidate run failed with `cleanup_done: unbound
variable`: a function-local flag was read by an EXIT trap after the function
had returned. The old design also deleted its temporary environment on exit
even though it only printed manual steps, and isolated only the mission topic.

These tests run the REAL script against a stateful fake `gcloud`
(fake_gcloud.py) inside a throwaway git repository whose HEAD is clean and
pushed, so the real preflight guards pass genuinely. No real GCP resource is
ever touched.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import fake_gcloud
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DEPLOY_SCRIPT = REPO_ROOT / "infrastructure" / "deploy_candidate_enhanced.sh"
PROMOTE_SCRIPT = REPO_ROOT / "infrastructure" / "promote_candidate_to_production.sh"
FAKE_GCLOUD = Path(__file__).resolve().parent / "fake_gcloud.py"

_GIT_BASH_CANDIDATES = [r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"]
BASH = next((p for p in _GIT_BASH_CANDIDATES if Path(p).exists()), "bash")

STUB_HELPER = '''\
import os, sys
with open(os.environ["FAKE_GCLOUD_LOG"], "a", encoding="utf-8") as fh:
    fh.write("HELPER " + " ".join(sys.argv[1:]) + "\\n")
if os.environ.get("STUB_HELPER_FAIL") == sys.argv[1]:
    print("VERIFICATION FAILED: injected", file=sys.stderr)
    sys.exit(1)
print("{}")
'''

MISSION = "MIS-1A2B3C4D"


def _posix(p: Path | str) -> str:
    return str(p).replace("\\", "/")


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class Lab:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        origin, self.repo = tmp / "origin.git", tmp / "repo"
        self.bin = tmp / "bin"
        self.bin.mkdir()
        subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)
        self.repo.mkdir()
        _git(self.repo, "init", "-b", "main")
        for k, v in (("user.email", "t@example.com"), ("user.name", "t"), ("core.autocrlf", "false")):
            _git(self.repo, "config", k, v)
        (self.repo / "infrastructure").mkdir()
        (self.repo / "backend" / "scripts").mkdir(parents=True)
        shutil.copy(DEPLOY_SCRIPT, self.repo / "infrastructure" / "deploy_candidate_enhanced.sh")
        promote = PROMOTE_SCRIPT.read_bytes().replace(b"\r\n", b"\n")
        (self.repo / "infrastructure" / "promote_candidate_to_production.sh").write_bytes(promote)
        (self.repo / "backend" / "scripts" / "verify_candidate_mission.py").write_text(STUB_HELPER, encoding="utf-8")
        (self.repo / ".gitignore").write_text("infrastructure/.candidate-verified/\n", encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-m", "init")
        _git(self.repo, "remote", "add", "origin", str(origin))
        _git(self.repo, "push", "-u", "origin", "main")
        self.sha = _git(self.repo, "rev-parse", "HEAD")
        self.short = self.sha[:12]

        self.state_file, self.log_file = tmp / "gcloud-state.json", tmp / "gcloud.log"
        self.log_file.write_text("", encoding="utf-8")
        subprocess.run([sys.executable, str(FAKE_GCLOUD), "--init", str(self.state_file)], check=True)
        py = _posix(sys.executable)
        (self.bin / "gcloud").write_text(
            f'#!/usr/bin/env bash\nexec "{py}" "{_posix(FAKE_GCLOUD)}" "$@"\n', encoding="utf-8", newline="\n")
        (self.bin / "python3").write_text(f'#!/usr/bin/env bash\nexec "{py}" "$@"\n', encoding="utf-8", newline="\n")
        self.marker_dir = self.repo / "infrastructure" / ".candidate-verified"
        self.pending = self.marker_dir / f"{self.sha}.pending.json"
        self.mission_topic = f"assurance-runs-candidate-verify-{self.short}"
        self.impact_topic = f"impact-batches-candidate-verify-{self.short}"

    def run(self, *args: str, script: str = "deploy_candidate_enhanced.sh", **env: str) -> subprocess.CompletedProcess:
        full_env = {
            **os.environ,
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "FAKE_GCLOUD_STATE": str(self.state_file), "FAKE_GCLOUD_LOG": str(self.log_file),
            "VERIFY_PYTHON": _posix(sys.executable), "PYTHONUTF8": "1",
            **env,
        }
        for k in ("FAKE_FAIL", "FAKE_MUTATE_PROD", "STUB_HELPER_FAIL"):
            if k not in env:
                full_env.pop(k, None)
        return subprocess.run(
            [BASH, f"infrastructure/{script}", *args], cwd=self.repo, env=full_env,
            capture_output=True, text=True, timeout=120, check=False,
        )

    def state(self) -> dict:
        return json.loads(self.state_file.read_text(encoding="utf-8"))

    def log(self) -> list[str]:
        return self.log_file.read_text(encoding="utf-8").splitlines()

    def mutate_state(self, fn) -> None:
        st = self.state()
        fn(st)
        self.state_file.write_text(json.dumps(st), encoding="utf-8")

    def pending_state(self) -> dict:
        return json.loads(self.pending.read_text(encoding="utf-8"))

    def prepare(self) -> subprocess.CompletedProcess:
        result = self.run("--prepare-verification")
        assert result.returncode == 0, result.stdout + result.stderr
        return result

    def temp_names(self) -> set[str]:
        st = self.state()
        return {n for n in [*st["topics"], *st["subs"]] if "candidate-verify" in n}

    def assert_pristine(self) -> None:
        """Candidate env at the original values, no temp resources, nothing pending."""
        st = self.state()
        assert st["services"]["rateguard-api"]["env"]["RATEGUARD_PUBSUB_TOPIC"] == "assurance-runs"
        assert st["services"]["rateguard-api"]["env"]["RATEGUARD_IMPACT_TOPIC"] == "impact-batches"
        assert st["services"]["rateguard-worker"]["env"]["RATEGUARD_IMPACT_TOPIC"] == "impact-batches"
        assert self.temp_names() == set()
        assert not self.pending.exists()
        assert fake_gcloud.PROD_TOPICS <= set(st["topics"])


@pytest.fixture
def lab(tmp_path: Path) -> Lab:
    return Lab(tmp_path)


# --- static guards on the script text -------------------------------------


def _script_text() -> str:
    return DEPLOY_SCRIPT.read_text(encoding="utf-8")


def test_trap_state_variables_are_initialised_top_level_before_any_trap() -> None:
    text = _script_text()
    first_trap = re.search(r"^\s*trap ", text, re.M).start()
    for var in ("PREPARE_ARMED=false", "CLEANUP_DONE=false"):
        assert text.index(f"\n{var}\n") < first_trap, f"{var} must be initialised before any trap is installed"
    assert "local cleanup_done" not in text
    assert "cleanup_done" not in text.replace("CLEANUP_DONE", "")
    assert "set -euo pipefail" in text
    for sig in ("INT", "TERM", "HUP", "EXIT"):
        assert re.search(rf"^\s*trap [^\n]*\b{sig}\b", text, re.M), f"no trap for {sig}"


def test_old_verify_candidate_mode_is_removed_with_guidance(lab: Lab) -> None:
    result = lab.run("--verify-candidate")
    assert result.returncode == 2
    assert "--prepare-verification" in result.stderr
    assert lab.log() == []


def test_argument_validation(lab: Lab) -> None:
    assert lab.run("--bogus").returncode == 2
    assert lab.run("--prepare-verification", "--abort-verification").returncode == 2
    assert lab.run("--complete-verification", "--mission-id=not-an-id").returncode == 2
    assert lab.run("--prepare-verification", f"--mission-id={MISSION}").returncode == 2
    assert lab.run("--complete-verification").returncode != 0  # no pending state / no mission id
    assert not any(line.startswith(("run services update", "pubsub topics create")) for line in lab.log())


def test_default_plan_documents_three_step_lifecycle_and_both_isolated_topics(lab: Lab) -> None:
    out = lab.run().stdout
    for expected in (
        "--prepare-verification", "--complete-verification --mission-id=<ID>", "--abort-verification",
        "impact-batches-candidate-verify-<sha12>", "assurance-runs-candidate-verify-<sha12>",
        "STABLE, untagged", "/internal/pubsub/impact-batch",
    ):
        assert expected in out, expected
    assert "--verify-candidate (run separately" not in out


# --- --prepare-verification ------------------------------------------------


def test_prepare_success_persists_pending_state_and_leaves_environment_in_place(lab: Lab) -> None:
    result = lab.prepare()
    assert lab.pending.exists()
    ps = lab.pending_state()
    assert ps["phase"] == "ready"
    assert ps["git_sha"] == lab.sha and len(ps["git_sha"]) == 40
    assert ps["mission_topic"] == lab.mission_topic and ps["impact_topic"] == lab.impact_topic
    assert ps["mission_subscription"] == lab.mission_topic + "-sub"
    assert ps["impact_subscription"] == lab.impact_topic + "-sub"
    assert ps["original_api_pubsub_topic"] == "assurance-runs"
    assert ps["original_api_impact_topic"] == "impact-batches"
    assert ps["original_worker_impact_topic"] == "impact-batches"
    assert ps["worker_stable_url"] == fake_gcloud.STABLE_WORKER_URL
    assert ps["worker_tagged_url"] == fake_gcloud.tag_url("rateguard-worker")
    assert ps["candidate_worker_image_digest"] == "sha256:" + "b" * 64
    for key in ("rating_engine_image", "worker_image", "api_image", "web_image"):
        assert "@sha256:" in ps[key]
    # The FINAL candidate worker revision is the one after the temporary env change.
    st = lab.state()
    assert ps["candidate_worker_revision"] == st["services"]["rateguard-worker"]["cand_rev"]
    assert ps["candidate_worker_revision"] != ps["initial_candidate_worker_revision"]
    assert ps["production_worker_revision"] == st["services"]["rateguard-worker"]["prod_rev"]
    # The environment stays available for the observed manual mission.
    assert lab.temp_names() == {lab.mission_topic, lab.mission_topic + "-sub", lab.impact_topic, lab.impact_topic + "-sub"}
    assert st["services"]["rateguard-api"]["env"]["RATEGUARD_PUBSUB_TOPIC"] == lab.mission_topic
    # Operator instructions.
    assert fake_gcloud.tag_url("rateguard-web") in result.stdout
    assert f"[CANDIDATE-VERIFY-{lab.short}]" in result.stdout
    assert "--complete-verification --mission-id=<MISSION_ID>" in result.stdout
    assert "--abort-verification" in result.stdout
    # No credential of any kind is stored or printed.
    # (The instructions may MENTION not pasting passwords; no secret-shaped value appears anywhere.)
    state_text = lab.pending.read_text(encoding="utf-8").lower()
    for word in ("password", "bearer", "id_token", "idtoken", "authorization", "access_token"):
        assert word not in state_text, word
    assert not re.search(r"eyj[a-z0-9_-]{10,}|bearer [a-z0-9]", (state_text + result.stdout + result.stderr).lower())
    # Production revisions never moved.
    assert st["services"]["rateguard-worker"]["prod_rev"] == "rateguard-worker-00001-prd"


def test_prepare_isolates_both_mission_and_impact_paths(lab: Lab) -> None:
    lab.prepare()
    st = lab.state()
    env_api, env_worker = st["services"]["rateguard-api"]["env"], st["services"]["rateguard-worker"]["env"]
    assert env_api["RATEGUARD_PUBSUB_TOPIC"] == lab.mission_topic
    assert env_api["RATEGUARD_IMPACT_TOPIC"] == lab.impact_topic
    assert env_worker["RATEGUARD_IMPACT_TOPIC"] == lab.impact_topic
    assert "assurance-runs" not in {env_api["RATEGUARD_PUBSUB_TOPIC"]}
    worker_url, stable = fake_gcloud.tag_url("rateguard-worker"), fake_gcloud.STABLE_WORKER_URL
    mission_sub, impact_sub = st["subs"][lab.mission_topic + "-sub"], st["subs"][lab.impact_topic + "-sub"]
    assert mission_sub["topic"] == lab.mission_topic
    assert mission_sub["pushConfig"]["pushEndpoint"] == f"{worker_url}/internal/pubsub/assurance"
    assert impact_sub["topic"] == lab.impact_topic
    assert impact_sub["pushConfig"]["pushEndpoint"] == f"{worker_url}/internal/pubsub/impact-batch"
    for sub in (mission_sub, impact_sub):
        assert sub["pushConfig"]["oidcToken"]["audience"] == stable  # stable URL, never a --tag URL
        assert "candidate---" not in sub["pushConfig"]["oidcToken"]["audience"]
    # No subscription exists on either production topic except the two production ones.
    prod_subs = {n for n, s in st["subs"].items() if s["topic"].split("/")[-1] in fake_gcloud.PROD_TOPICS}
    assert prod_subs == {"assurance-runs-worker-sub", "impact-batches-worker-sub"}
    # No production topic/subscription was ever a target of a mutating call.
    for line in lab.log():
        if re.search(r"pubsub (topics|subscriptions) (create|delete|add-iam-policy-binding|modify-push-config)", line):
            assert not re.search(r"\b(assurance-runs|impact-batches)(-worker-sub)?\b(?!-candidate)", line.replace(lab.mission_topic, "").replace(lab.impact_topic, "")), line
        assert "publish" not in line.split(" ")[:3]
        assert "--topic=assurance-runs " not in line + " " and "--topic=impact-batches " not in line + " "


def test_prepare_failure_cleans_up_without_unbound_variable_error(lab: Lab) -> None:
    result = lab.run("--prepare-verification", FAKE_FAIL=f"subscriptions create {lab.impact_topic}")
    assert result.returncode != 0
    assert "unbound variable" not in result.stderr + result.stdout
    assert "PREPARATION FAILED" in result.stderr
    lab.assert_pristine()
    assert not (lab.marker_dir / f"{lab.sha}.prod-subs").exists()


def test_prepare_failure_after_worker_env_change_restores_worker_env(lab: Lab) -> None:
    result = lab.run("--prepare-verification", FAKE_FAIL="services update rateguard-api")
    assert result.returncode != 0 and "unbound variable" not in result.stderr
    lab.assert_pristine()
    # The API env was never changed, so cleanup must not issue a needless second update for it.
    assert sum(1 for line in lab.log() if line.startswith("run services update rateguard-api")) == 1


def test_abort_that_cannot_finish_retains_pending_state_and_can_be_repeated(lab: Lab) -> None:
    lab.prepare()
    failed = lab.run("--abort-verification", FAKE_FAIL="subscriptions delete")
    assert failed.returncode != 0
    assert lab.pending.exists(), "pending state must be retained when cleanup could not finish"
    assert "re-run" in failed.stderr.lower()
    assert not (lab.marker_dir / f"{lab.sha}.aborted").exists()
    assert lab.run("--abort-verification").returncode == 0
    lab.assert_pristine()


def test_prepare_refuses_when_a_verification_is_already_pending(lab: Lab) -> None:
    lab.prepare()
    again = lab.run("--prepare-verification")
    assert again.returncode != 0 and "already pending" in again.stderr
    assert lab.temp_names(), "the pending environment must be untouched by the refused second prepare"


def test_prepare_refuses_orphan_temp_resources(lab: Lab) -> None:
    lab.mutate_state(lambda st: st["topics"].update({lab.mission_topic: {}}))
    result = lab.run("--prepare-verification")
    assert result.returncode != 0 and "orphan" in result.stderr
    assert not lab.pending.exists()


def test_prepare_refuses_candidate_serving_traffic(lab: Lab) -> None:
    lab.mutate_state(lambda st: st["services"]["rateguard-api"].update(cand_percent=10))
    result = lab.run("--prepare-verification")
    assert result.returncode != 0 and "must be 0%" in result.stderr
    assert not lab.pending.exists() and lab.temp_names() == set()


def test_prepare_refuses_candidate_identical_to_production(lab: Lab) -> None:
    lab.mutate_state(lambda st: st["services"]["rateguard-worker"].update(cand_rev=st["services"]["rateguard-worker"]["prod_rev"]))
    result = lab.run("--prepare-verification")
    assert result.returncode != 0 and "not distinct from production" in result.stderr
    assert not lab.pending.exists() and lab.temp_names() == set()


# --- --abort-verification --------------------------------------------------


def test_abort_restores_deletes_only_temp_resources_and_is_idempotent(lab: Lab) -> None:
    lab.prepare()
    first = lab.run("--abort-verification")
    assert first.returncode == 0, first.stdout + first.stderr
    lab.assert_pristine()
    assert (lab.marker_dir / f"{lab.sha}.aborted").exists()
    assert not (lab.marker_dir / f"{lab.sha}.prod-subs").exists()
    updates_after_first = sum(1 for line in lab.log() if line.startswith("run services update"))
    second = lab.run("--abort-verification")
    assert second.returncode == 0 and "nothing to restore" in second.stdout
    assert sum(1 for line in lab.log() if line.startswith("run services update")) == updates_after_first
    for line in lab.log():
        if re.search(r"pubsub (topics|subscriptions) delete", line):
            assert "candidate-verify" in line, f"abort deleted a non-temporary resource: {line}"


def test_abort_works_when_some_resources_are_already_absent(lab: Lab) -> None:
    lab.prepare()
    lab.mutate_state(lambda st: (st["subs"].pop(lab.mission_topic + "-sub"), st["topics"].pop(lab.impact_topic)))
    result = lab.run("--abort-verification")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "already absent" in result.stdout
    lab.assert_pristine()


def test_abort_works_when_env_was_already_restored_by_hand(lab: Lab) -> None:
    lab.prepare()

    def restore(st: dict) -> None:
        st["services"]["rateguard-api"]["env"].update(RATEGUARD_PUBSUB_TOPIC="assurance-runs", RATEGUARD_IMPACT_TOPIC="impact-batches")
        st["services"]["rateguard-worker"]["env"]["RATEGUARD_IMPACT_TOPIC"] = "impact-batches"

    lab.mutate_state(restore)
    before = sum(1 for line in lab.log() if line.startswith("run services update"))
    assert lab.run("--abort-verification").returncode == 0
    assert sum(1 for line in lab.log() if line.startswith("run services update")) == before
    lab.assert_pristine()


def test_abort_refuses_a_tampered_state_file_and_never_deletes_production_names(lab: Lab) -> None:
    lab.prepare()
    ps = lab.pending_state()
    ps["mission_topic"] = "assurance-runs"
    ps["mission_subscription"] = "assurance-runs-worker-sub"
    lab.pending.write_text(json.dumps(ps), encoding="utf-8")
    result = lab.run("--abort-verification")
    assert result.returncode != 0 and "does not match" in result.stderr
    st = lab.state()
    assert {"assurance-runs", "impact-batches"} <= set(st["topics"])
    assert {"assurance-runs-worker-sub", "impact-batches-worker-sub"} <= set(st["subs"])
    assert not any(re.search(r"delete (assurance-runs|impact-batches)(-worker-sub)?$", line) for line in lab.log())


def test_abort_of_a_prepare_that_never_ran_is_a_noop(lab: Lab) -> None:
    result = lab.run("--abort-verification")
    assert result.returncode == 0 and "nothing to restore" in result.stdout
    assert not any(line.startswith("run services update") for line in lab.log())


# --- --complete-verification -----------------------------------------------


def test_complete_success_restores_deletes_pins_digests_and_clears_pending(lab: Lab) -> None:
    lab.prepare()
    verified_worker_rev = lab.pending_state()["candidate_worker_revision"]
    result = lab.run("--complete-verification", f"--mission-id={MISSION}")
    assert result.returncode == 0, result.stdout + result.stderr
    lab.assert_pristine()
    assert not (lab.marker_dir / f"{lab.sha}.prod-subs").exists()
    helper_calls = [line for line in lab.log() if line.startswith("HELPER")]
    assert helper_calls == [f"HELPER check --state-file infrastructure/.candidate-verified/{lab.sha}.pending.json --mission-id {MISSION}",
                            f"HELPER duplicate --state-file infrastructure/.candidate-verified/{lab.sha}.pending.json --mission-id {MISSION}"]
    evidence = (lab.marker_dir / f"{lab.sha}.evidence").read_text(encoding="utf-8")
    kv = dict(line.split("=", 1) for line in evidence.splitlines() if "=" in line)
    assert kv["GIT_SHA"] == lab.sha and kv["VERIFICATION_COMPLETE"] == "true" and kv["MISSION_ID"] == MISSION
    assert kv["VERIFIED_WORKER_REVISION"] == verified_worker_rev
    for name, image in (("RATING_ENGINE", fake_gcloud.IMAGES["rateguard-rating-engine"]), ("WORKER", fake_gcloud.BACKEND_IMAGE),
                        ("API", fake_gcloud.BACKEND_IMAGE), ("WEB", fake_gcloud.IMAGES["rateguard-web"])):
        assert kv[f"{name}_DIGEST"] == image, name
    st = lab.state()
    assert kv["WORKER_REVISION"] == st["services"]["rateguard-worker"]["cand_rev"]  # post-restore, what promotion will see
    assert kv["API_REVISION"] == st["services"]["rateguard-api"]["cand_rev"]
    # Completion alone never writes the promotion marker.
    assert not (lab.marker_dir / lab.sha).exists()
    assert lab.run("--record-verified").returncode == 0
    assert (lab.marker_dir / lab.sha).exists()


@pytest.mark.parametrize("failing_step", ["check", "duplicate"])
def test_complete_failure_refuses_promotion_and_keeps_the_environment(lab: Lab, failing_step: str) -> None:
    lab.prepare()
    result = lab.run("--complete-verification", f"--mission-id={MISSION}", STUB_HELPER_FAIL=failing_step)
    assert result.returncode != 0 and "NOT complete" in result.stderr
    assert lab.pending.exists()
    assert len(lab.temp_names()) == 4, "a failed check must not tear down the environment the operator may re-test in"
    assert not (lab.marker_dir / f"{lab.sha}.evidence").exists()
    assert lab.run("--record-verified").returncode != 0
    assert not (lab.marker_dir / lab.sha).exists()
    # ...and a later abort still cleans everything.
    assert lab.run("--abort-verification").returncode == 0
    lab.assert_pristine()


def test_complete_requires_pending_state_and_mission_id(lab: Lab) -> None:
    no_pending = lab.run("--complete-verification", f"--mission-id={MISSION}")
    assert no_pending.returncode != 0 and "no pending verification" in no_pending.stderr
    lab.prepare()
    no_id = lab.run("--complete-verification")
    assert no_id.returncode == 2 and "--mission-id" in no_id.stderr
    assert not any(line.startswith("HELPER") for line in lab.log())


def test_complete_refuses_when_a_candidate_revision_moved_after_prepare(lab: Lab) -> None:
    lab.prepare()
    lab.run("--abort-verification")  # ensure a clean baseline for the mutation below
    lab.prepare()
    lab.mutate_state(lambda st: st["services"]["rateguard-worker"].update(cand_rev="rateguard-worker-99999-new",
                     images={**st["services"]["rateguard-worker"]["images"], "rateguard-worker-99999-new": fake_gcloud.BACKEND_IMAGE}))
    result = lab.run("--complete-verification", f"--mission-id={MISSION}")
    assert result.returncode != 0 and "changed since preparation" in result.stderr
    assert not any(line.startswith("HELPER") for line in lab.log())


# --- production subscription immutability -----------------------------------


def test_production_subscription_drift_during_prepare_is_reported_and_state_retained(lab: Lab) -> None:
    result = lab.run("--prepare-verification", FAKE_MUTATE_PROD="1")
    assert result.returncode != 0
    assert "SUSPECT" in result.stderr
    assert lab.pending.exists(), "cleanup could not be confirmed, so the pending state must be retained"


def test_production_subscription_drift_blocks_completion(lab: Lab) -> None:
    lab.prepare()
    lab.mutate_state(lambda st: st["subs"]["impact-batches-worker-sub"].update(ackDeadlineSeconds=1))
    result = lab.run("--complete-verification", f"--mission-id={MISSION}")
    assert result.returncode != 0 and "CHANGED during verification" in result.stderr
    assert not (lab.marker_dir / f"{lab.sha}.evidence").exists()
    assert lab.pending.exists()


def test_production_subscription_drift_is_reported_by_abort(lab: Lab) -> None:
    lab.prepare()
    lab.mutate_state(lambda st: st["subs"]["assurance-runs-worker-sub"].update(ackDeadlineSeconds=1))
    result = lab.run("--abort-verification")
    assert result.returncode != 0 and "SUSPECT" in result.stderr
    assert lab.pending.exists()
    assert lab.temp_names() == set(), "the temporary resources are still deleted even when drift is detected"


def test_both_production_subscriptions_are_recorded_byte_for_byte(lab: Lab) -> None:
    lab.prepare()
    snap = lab.marker_dir / f"{lab.sha}.prod-subs"
    for name in ("assurance-runs-worker-sub", "impact-batches-worker-sub"):
        assert json.loads((snap / f"{name}.json").read_text(encoding="utf-8")) == lab.state()["subs"][name]


# --- refusal to record / promote incomplete verification ---------------------


def test_record_verified_refuses_without_completed_verification(lab: Lab) -> None:
    result = lab.run("--record-verified")
    assert result.returncode != 0 and "no completed verification" in result.stderr
    assert not (lab.marker_dir / lab.sha).exists()


def test_record_verified_refuses_forged_or_incomplete_evidence(lab: Lab) -> None:
    lab.marker_dir.mkdir(parents=True)
    (lab.marker_dir / f"{lab.sha}.evidence").write_text(f"GIT_SHA={lab.sha}\nMISSION_ID={MISSION}\n", encoding="utf-8")
    assert lab.run("--record-verified").returncode != 0
    assert not (lab.marker_dir / lab.sha).exists()


def test_record_verified_refuses_pending_and_aborted(lab: Lab) -> None:
    lab.prepare()
    pending = lab.run("--record-verified")
    assert pending.returncode != 0 and "still pending" in pending.stderr
    assert lab.run("--abort-verification").returncode == 0
    aborted = lab.run("--record-verified")
    assert aborted.returncode != 0 and "aborted" in aborted.stderr
    assert not (lab.marker_dir / lab.sha).exists()


def test_record_verified_refuses_when_candidate_moved_after_completion(lab: Lab) -> None:
    lab.prepare()
    assert lab.run("--complete-verification", f"--mission-id={MISSION}").returncode == 0
    lab.mutate_state(lambda st: st["services"]["rateguard-web"].update(cand_rev="rateguard-web-00009-new",
                     images={**st["services"]["rateguard-web"]["images"], "rateguard-web-00009-new": "other@sha256:" + "d" * 64}))
    result = lab.run("--record-verified")
    assert result.returncode != 0 and "moved since verification" in result.stderr


def test_a_new_prepare_invalidates_earlier_verification_artifacts(lab: Lab) -> None:
    lab.prepare()
    assert lab.run("--complete-verification", f"--mission-id={MISSION}").returncode == 0
    assert lab.run("--record-verified").returncode == 0
    lab.prepare()
    assert not (lab.marker_dir / lab.sha).exists() and not (lab.marker_dir / f"{lab.sha}.evidence").exists()


@pytest.mark.parametrize("skip", [[], ["--skip-verification-check"]])
def test_promotion_refuses_a_pending_verification(lab: Lab, skip: list[str]) -> None:
    lab.prepare()
    result = lab.run("--promote", *skip, script="promote_candidate_to_production.sh")
    assert result.returncode != 0 and "PENDING" in result.stderr
    assert not any("update-traffic" in line for line in lab.log())


@pytest.mark.parametrize("skip", [[], ["--skip-verification-check"]])
def test_promotion_refuses_an_aborted_verification(lab: Lab, skip: list[str]) -> None:
    lab.prepare()
    assert lab.run("--abort-verification").returncode == 0
    result = lab.run("--promote", *skip, script="promote_candidate_to_production.sh")
    assert result.returncode != 0 and "ABORTED" in result.stderr


def test_promotion_refuses_without_any_verification(lab: Lab) -> None:
    result = lab.run("--promote", script="promote_candidate_to_production.sh")
    assert result.returncode != 0 and "no verified marker" in result.stderr


def test_promotion_refuses_evidence_not_from_a_completed_verification(lab: Lab) -> None:
    lab.marker_dir.mkdir(parents=True)
    (lab.marker_dir / lab.sha).write_text("verified\n", encoding="utf-8")
    (lab.marker_dir / f"{lab.sha}.evidence").write_text(f"GIT_SHA={lab.sha}\n", encoding="utf-8")
    result = lab.run("--promote", script="promote_candidate_to_production.sh")
    assert result.returncode != 0 and "not from a completed" in result.stderr


def test_preflight_guards_still_block_every_mutating_verification_mode(lab: Lab) -> None:
    (lab.repo / "dirty.txt").write_text("x", encoding="utf-8")
    _git(lab.repo, "add", "dirty.txt")
    for mode in (["--prepare-verification"], ["--complete-verification", f"--mission-id={MISSION}"], ["--abort-verification"]):
        result = lab.run(*mode)
        assert result.returncode != 0 and "uncommitted changes" in result.stderr, mode
    _git(lab.repo, "commit", "-m", "unpushed")
    result = lab.run("--prepare-verification")
    assert result.returncode != 0 and "unpushed" in result.stderr
    assert not any(line.startswith(("run services update", "pubsub")) for line in lab.log())
