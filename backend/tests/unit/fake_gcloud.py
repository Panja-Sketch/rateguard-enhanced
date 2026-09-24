"""A stateful, in-memory stand-in for the `gcloud` CLI, used ONLY by
test_candidate_verification_lifecycle.py to drive deploy_candidate_enhanced.sh
without touching any real GCP resource.

State lives in a JSON file (FAKE_GCLOUD_STATE); every invocation is appended to
FAKE_GCLOUD_LOG. Failure/mutation injection (env):

  FAKE_FAIL          fail any call whose joined argv contains this substring
  FAKE_MUTATE_PROD   on any `topics create`, silently rewrite a production
                     subscription's config (simulates production drift)
"""

from __future__ import annotations

import json
import os
import sys

STABLE_WORKER_URL = "https://rateguard-worker-abc123-uc.a.run.app"
PROD_TOPICS = {"assurance-runs", "impact-batches"}
SERVICES = ("rateguard-api", "rateguard-worker", "rateguard-rating-engine", "rateguard-web")
BACKEND_IMAGE = "us-central1-docker.pkg.dev/rateguard-enhanced/rateguard-images/rateguard-api@sha256:" + "b" * 64
IMAGES = {
    "rateguard-api": BACKEND_IMAGE,
    "rateguard-worker": BACKEND_IMAGE,
    "rateguard-rating-engine": "us-central1-docker.pkg.dev/rateguard-enhanced/rateguard-images/rateguard-rating-engine@sha256:" + "a" * 64,
    "rateguard-web": "us-central1-docker.pkg.dev/rateguard-enhanced/rateguard-images/rateguard-web@sha256:" + "c" * 64,
}


def initial_state() -> dict:
    services = {}
    for svc in SERVICES:
        env = {}
        if svc == "rateguard-api":
            env = {"RATEGUARD_PUBSUB_TOPIC": "assurance-runs", "RATEGUARD_IMPACT_TOPIC": "impact-batches"}
        if svc == "rateguard-worker":
            env = {"RATEGUARD_PUBSUB_TOPIC": "assurance-runs", "RATEGUARD_IMPACT_TOPIC": "impact-batches"}
        services[svc] = {
            "env": env,
            "seq": 2,
            "prod_rev": f"{svc}-00001-prd",
            "cand_rev": f"{svc}-00002-cnd",
            "images": {f"{svc}-00001-prd": IMAGES[svc], f"{svc}-00002-cnd": IMAGES[svc]},
        }
    prod_sub = lambda name, topic, path: {  # noqa: E731
        "name": f"projects/rateguard-enhanced/subscriptions/{name}",
        "topic": f"projects/rateguard-enhanced/topics/{topic}",
        "pushConfig": {"pushEndpoint": f"{STABLE_WORKER_URL}{path}", "oidcToken": {"audience": STABLE_WORKER_URL}},
        "ackDeadlineSeconds": 600,
    }
    return {
        "topics": {"assurance-runs": {}, "impact-batches": {}},
        "subs": {
            "assurance-runs-worker-sub": prod_sub("assurance-runs-worker-sub", "assurance-runs", "/internal/pubsub/assurance"),
            "impact-batches-worker-sub": prod_sub("impact-batches-worker-sub", "impact-batches", "/internal/pubsub/impact-batch"),
        },
        "services": services,
    }


def tag_url(svc: str) -> str:
    return f"https://candidate---{svc}-abc123-uc.a.run.app"


def traffic(svc_state: dict, svc: str) -> list[dict]:
    return [
        {"percent": 100, "revisionName": svc_state["prod_rev"], "type": "TYPE_REVISION"},
        {"revisionName": svc_state["cand_rev"], "tag": "candidate", "url": tag_url(svc),
         **({"percent": svc_state["cand_percent"]} if svc_state.get("cand_percent") else {})},
    ]


def flag(args: list[str], name: str) -> str | None:
    for i, a in enumerate(args):
        if a == name and i + 1 < len(args):
            return args[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return None


def main(argv: list[str]) -> int:
    state_path, log_path = os.environ["FAKE_GCLOUD_STATE"], os.environ["FAKE_GCLOUD_LOG"]
    joined = " ".join(argv)
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(joined + "\n")
    fail = os.environ.get("FAKE_FAIL")
    if fail and fail in joined:
        print(f"ERROR: injected failure for: {joined}", file=sys.stderr)
        return 1
    with open(state_path, encoding="utf-8") as fh:
        st = json.load(fh)

    def save() -> None:
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump(st, fh)

    a = argv
    if a[:3] == ["config", "get-value", "project"]:
        print("rateguard-enhanced")
        return 0
    if a[:2] == ["config", "set"]:
        return 0

    if a[:3] == ["run", "services", "describe"]:
        svc = a[3]
        s = st["services"][svc]
        fmt = flag(a, "--format") or ""
        if fmt == "json":
            print(json.dumps({
                "status": {"traffic": traffic(s, svc), "url": STABLE_WORKER_URL if svc == "rateguard-worker" else f"https://{svc}-abc123-uc.a.run.app"},
                "spec": {"template": {"spec": {"containers": [{"env": [{"name": k, "value": v} for k, v in s["env"].items()]}]}}},
            }))
        elif fmt == "value(status.traffic)":
            print(";".join(str(t) for t in traffic(s, svc)))
        elif fmt == "value(status.url)":
            print(STABLE_WORKER_URL if svc == "rateguard-worker" else f"https://{svc}-abc123-uc.a.run.app")
        elif fmt == "value(status.latestCreatedRevisionName)":
            print(s["cand_rev"])
        else:
            print("")
        return 0

    if a[:3] == ["run", "services", "update"]:
        svc = a[3]
        s = st["services"][svc]
        updates = flag(a, "--update-env-vars")
        removes = flag(a, "--remove-env-vars")
        for pair in (updates or "").split(","):
            if pair:
                k, _, v = pair.partition("=")
                s["env"][k] = v
        for k in (removes or "").split(","):
            if k:
                s["env"].pop(k, None)
        s["seq"] += 1
        new_rev = f"{svc}-{s['seq']:05d}-cnd"
        s["images"][new_rev] = s["images"][s["cand_rev"]]
        s["cand_rev"] = new_rev
        save()
        return 0

    if a[:3] == ["run", "revisions", "describe"]:
        rev = a[3]
        for svc_state in st["services"].values():
            if rev in svc_state["images"]:
                print(svc_state["images"][rev])
                return 0
        return 1

    if a[:4] == ["artifacts", "docker", "images", "describe"]:
        print("sha256:" + "b" * 64)
        return 0

    if a[:2] == ["pubsub", "topics"]:
        verb, name = a[2], a[3]
        if verb == "describe":
            return 0 if name in st["topics"] else 1
        if verb == "create":
            st["topics"][name] = {}
            if os.environ.get("FAKE_MUTATE_PROD"):
                st["subs"]["assurance-runs-worker-sub"]["ackDeadlineSeconds"] = 10
            save()
            return 0
        if verb == "delete":
            st["topics"].pop(name, None)
            save()
            return 0
        if verb == "add-iam-policy-binding":
            return 0
    if a[:2] == ["pubsub", "subscriptions"]:
        verb, name = a[2], a[3]
        if verb == "describe":
            if name not in st["subs"]:
                return 1
            print(json.dumps(st["subs"][name], indent=2, sort_keys=True))
            return 0
        if verb == "create":
            st["subs"][name] = {
                "name": name, "topic": flag(a, "--topic"),
                "pushConfig": {
                    "pushEndpoint": flag(a, "--push-endpoint"),
                    "oidcToken": {"audience": flag(a, "--push-auth-token-audience"),
                                  "serviceAccountEmail": flag(a, "--push-auth-service-account")},
                },
            }
            save()
            return 0
        if verb == "delete":
            st["subs"].pop(name, None)
            save()
            return 0
    print(f"fake gcloud: unhandled command: {joined}", file=sys.stderr)
    return 99


def make_offline_env(tmp_dir) -> dict[str, str]:
    """Environment whose PATH resolves `gcloud`/`python3` to this fake, so a
    script's read-only discovery never reaches the network."""
    from pathlib import Path

    tmp = Path(tmp_dir)
    bin_dir = tmp / "fake-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    state, log = tmp / "gcloud-state.json", tmp / "gcloud.log"
    with open(state, "w", encoding="utf-8") as fh:
        json.dump(initial_state(), fh)
    log.write_text("", encoding="utf-8")
    py, me = sys.executable.replace("\\", "/"), str(Path(__file__).resolve()).replace("\\", "/")
    shim = '#!/usr/bin/env bash\nexec "{py}" {target}"$@"\n'
    (bin_dir / "gcloud").write_text(shim.format(py=py, target=f'"{me}" '), encoding="utf-8", newline="\n")
    (bin_dir / "python3").write_text(shim.format(py=py, target=""), encoding="utf-8", newline="\n")
    return {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
            "FAKE_GCLOUD_STATE": str(state), "FAKE_GCLOUD_LOG": str(log)}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--init":
        with open(sys.argv[2], "w", encoding="utf-8") as fh:
            json.dump(initial_state(), fh)
        sys.exit(0)
    sys.exit(main(sys.argv[1:]))
