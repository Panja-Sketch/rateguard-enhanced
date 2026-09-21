"""Deployment/IAM safety guards for the connector-impact release."""

import re
from pathlib import Path

import yaml

from app.impact.config import resolve_impact_config

ROOT = Path(__file__).resolve().parents[3]
INFRA = ROOT / "infrastructure"


def test_runtime_env_impact_budgets_are_valid_and_safe():
    env = yaml.safe_load((INFRA / "runtime-env.rateguard-enhanced.yaml").read_text(encoding="utf-8"))
    impact_env = {k: v for k, v in env.items() if k.startswith("RATEGUARD_IMPACT_") and k != "RATEGUARD_IMPACT_TOPIC"}
    assert impact_env, "impact budgets must be declared explicitly"
    config = resolve_impact_config(impact_env)
    assert config.max_policies == 50_000 and config.global_request_concurrency <= 20


def test_no_deploy_or_infra_file_maps_legacy_credentials():
    for path in list(INFRA.glob("*.sh")) + list(INFRA.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"--(set|update)-secrets[= ]\S+=\S+", text), path.name  # no actual secret mapping
        assert not re.search(r"(?m)^\s*(export\s+)?(GEMINI_API_KEY|GOOGLE_API_KEY)\s*[=:]", text), path.name
        assert not re.search(r"--(set|update)-env-vars[= ]\S*(GEMINI_API_KEY|GOOGLE_API_KEY)=", text), path.name


def test_impact_pubsub_script_is_resource_scoped_and_private():
    text = (INFRA / "setup_impact_pubsub.sh").read_text(encoding="utf-8")
    assert "allUsers" not in text and "allAuthenticatedUsers" not in text
    assert "--push-auth-service-account" in text and "--push-auth-token-audience" in text
    assert "--dead-letter-topic" in text and "--max-delivery-attempts=5" in text
    assert "add-iam-policy-binding" in text  # bindings are per topic/subscription, never project-wide
    assert "add-iam-policy-binding \"$PROJECT\"" not in text and "projects add-iam-policy-binding" not in text


def test_firestore_rules_still_deny_every_client_and_impact_ttl_is_declared():
    import json

    rules = (INFRA / "firestore.rules").read_text(encoding="utf-8")
    assert "allow read, write: if false;" in rules and "allow read" not in rules.replace("allow read, write: if false;", "")
    indexes = json.loads((INFRA / "firestore.indexes.json").read_text(encoding="utf-8"))
    assert any(o["collectionGroup"] == "batches" and o["fieldPath"] == "expires_at" and o["ttl"]
               for o in indexes["fieldOverrides"])
