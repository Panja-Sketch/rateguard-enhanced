"""Tests for the individual deployment-verification gate functions in
scripts/verify_deployment.py. Only offline checks are exercised here — no
gcloud, no network, no live Gemini, no production Firestore/Pub/Sub.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import verify_deployment as vd  # noqa: E402


def test_google_api_core_known_bad_version_fails() -> None:
    with patch("importlib.metadata.version", return_value="2.35.0"):
        result = vd.check_google_api_core_version()
    assert result.passed is False
    assert "2.35.0" in result.detail


def test_google_api_core_pinned_version_passes() -> None:
    with patch("importlib.metadata.version", return_value="2.34.0"):
        result = vd.check_google_api_core_version()
    assert result.passed is True


def test_worker_dispatch_check_passes_on_current_codebase() -> None:
    """The whole point of this hardening pass: the worker must route MIS-*
    jobs through MissionExecutionService, not construct AssuranceMission
    inline."""
    result = vd.check_worker_dispatches_via_mission_execution_service()
    assert result.passed is True


def test_worker_dispatch_check_fails_if_dispatch_reference_missing() -> None:
    fake_worker_cls = MagicMock()

    def _fake_process_job(self, job):
        """Old pre-refactor shape with no MissionExecutionService reference."""
        return None

    with patch("app.messaging.worker.AssuranceWorker", fake_worker_cls):
        with patch("inspect.getsource", return_value="def process_job(self, job):\n    return None\n"):
            result = vd.check_worker_dispatches_via_mission_execution_service()
    assert result.passed is False
    assert "MissionExecutionService" in result.detail


def test_gemini_auth_mode_none_while_enabled_fails() -> None:
    fake_config = MagicMock(agent_enabled=True)
    with (
        patch("app.agents.config.get_agent_config", return_value=fake_config),
        patch("app.agents.gemini_client.GeminiDecisionClient") as mock_client_cls,
    ):
        mock_client_cls.return_value._resolve_auth_mode.return_value = ("NONE", {})
        with patch("app.agents.gemini_client.AUTH_MODE_NONE", "NONE"):
            result = vd.check_gemini_auth_mode()
    assert result.passed is False
    assert "no auth mode resolves" in result.detail


def test_gemini_auth_mode_disabled_is_not_applicable() -> None:
    fake_config = MagicMock(agent_enabled=False)
    with patch("app.agents.config.get_agent_config", return_value=fake_config):
        result = vd.check_gemini_auth_mode()
    assert result.passed is True


def test_gemini_auth_mode_vertex_resolved_passes() -> None:
    fake_config = MagicMock(agent_enabled=True)
    with (
        patch("app.agents.config.get_agent_config", return_value=fake_config),
        patch("app.agents.gemini_client.GeminiDecisionClient") as mock_client_cls,
        patch("app.agents.gemini_client.AUTH_MODE_NONE", "NONE"),
    ):
        mock_client_cls.return_value._resolve_auth_mode.return_value = ("VERTEX_AI", {"vertexai": True})
        result = vd.check_gemini_auth_mode()
    assert result.passed is True


def test_gemini_model_id_wrong_value_fails() -> None:
    fake_config = MagicMock(gemini_model="gemini-1.0-pro")
    with patch("app.agents.config.get_agent_config", return_value=fake_config):
        result = vd.check_gemini_model_id()
    assert result.passed is False
    assert "gemini-1.0-pro" in result.detail


def test_gemini_model_id_correct_value_passes() -> None:
    fake_config = MagicMock(gemini_model="gemini-3.7-flash")
    with patch("app.agents.config.get_agent_config", return_value=fake_config):
        result = vd.check_gemini_model_id()
    assert result.passed is True


def test_worker_endpoint_ack_semantics_check_passes_on_current_codebase() -> None:
    result = vd.check_worker_endpoint_never_acks_retryable_failure()
    assert result.passed is True


def test_live_checks_are_skipped_without_targets() -> None:
    firestore_result = vd.check_firestore_readiness_via_health_endpoint(None)
    pubsub_result = vd.check_pubsub_push_endpoint(None)
    digest_result = vd.check_api_worker_image_parity(None, None)

    assert firestore_result.passed is None
    assert pubsub_result.passed is None
    assert digest_result.passed is None


def test_pubsub_push_endpoint_wrong_suffix_fails() -> None:
    with patch(
        "verify_deployment._gcloud_json",
        return_value=({"pushConfig": {"pushEndpoint": "https://example.com/wrong/path"}}, None),
    ):
        result = vd.check_pubsub_push_endpoint("rateguard-ai")
    assert result.passed is False


def test_pubsub_push_endpoint_correct_suffix_passes() -> None:
    with patch(
        "verify_deployment._gcloud_json",
        return_value=(
            {"pushConfig": {"pushEndpoint": "https://rateguard-worker-x.a.run.app/internal/pubsub/assurance"}},
            None,
        ),
    ):
        result = vd.check_pubsub_push_endpoint("rateguard-ai")
    assert result.passed is True


def test_image_digest_parity_mismatch_fails() -> None:
    def _fake_gcloud_json(args):
        if "rateguard-api" in args:
            return {"status": {"traffic": [{"imageDigest": "sha256:aaa"}]}}, None
        return {"status": {"traffic": [{"imageDigest": "sha256:bbb"}]}}, None

    with patch("verify_deployment._gcloud_json", side_effect=_fake_gcloud_json):
        result = vd.check_api_worker_image_parity("rateguard-ai", "us-central1")
    assert result.passed is False


def test_image_digest_parity_match_passes() -> None:
    def _fake_gcloud_json(args):
        return {"status": {"traffic": [{"imageDigest": "sha256:same"}]}}, None

    with patch("verify_deployment._gcloud_json", side_effect=_fake_gcloud_json):
        result = vd.check_api_worker_image_parity("rateguard-ai", "us-central1")
    assert result.passed is True


def test_main_exits_nonzero_when_offline_check_fails() -> None:
    with patch("verify_deployment.run_offline_checks", return_value=[vd.CheckResult("fake", False, "boom")]):
        assert vd.main(argv=[]) == 1


def test_main_exits_zero_when_all_offline_checks_pass() -> None:
    with patch("verify_deployment.run_offline_checks", return_value=[vd.CheckResult("fake", True, "ok")]):
        assert vd.main(argv=[]) == 0
