"""Detailed operational diagnostics for authorized operational use.

Unlike /health/live and /health/ready (fast, unauthenticated-safe probes), this
endpoint reports richer configuration/diagnostic detail intended for operators
debugging deployment issues (e.g. the "Mission V2 records remain QUEUED" class
of problem). It intentionally never performs a billable Gemini generation call
or a full BigQuery query - only configuration/reachability is reported. It never
exposes secret values, service account keys, or auth headers.
"""

from typing import Any

from fastapi import APIRouter

from app.core.config import get_settings

router = APIRouter(prefix="/api/v1/system", tags=["system-status"])


@router.get("/status")
def get_system_status() -> dict[str, Any]:
    """Detailed dependency diagnostics: Firestore, Pub/Sub configuration, worker
    heartbeat (per-mission, not a global signal - see note below), Gemini
    configuration (model id only, no invocation), BigQuery, and GCS. No secret
    values are ever included."""
    settings = get_settings()

    return {
        "run_store": _run_store_status(),
        "message_queue": _message_queue_status(settings),
        "worker_heartbeat": _worker_heartbeat_note(),
        "gemini": _gemini_status(),
        "bigquery": _bigquery_status(settings),
        "artifact_storage": _artifact_storage_status(settings),
    }


def _run_store_status() -> dict[str, Any]:
    import os

    from app.storage import get_run_store

    configured_backend = os.getenv("RATEGUARD_RUN_STORE", "memory")
    try:
        store = get_run_store()
        store_kind = type(store).__name__
        db = getattr(store, "_db", "n/a")
        firestore_client_ready = db is not None if store_kind == "FirestoreRunStore" else None
        return {
            "configured_backend": configured_backend,
            "active_backend": store_kind,
            "firestore_client_constructed": firestore_client_ready,
            "note": (
                "If firestore_client_constructed is false, this instance silently fell back to a "
                "per-instance, non-durable InMemoryRunStore - a mission created on one instance "
                "would be invisible to a worker instance with its own separate in-memory store."
                if firestore_client_ready is False
                else None
            ),
        }
    except Exception as e:
        return {"configured_backend": configured_backend, "error": str(e)}


def _message_queue_status(settings: Any) -> dict[str, Any]:
    return {
        "async_enabled": settings.async_enabled,
        "execution_mode": settings.execution_mode,
        "topic": settings.pubsub_topic,
        "subscription": settings.pubsub_subscription,
        "worker_push_route": "/internal/pubsub/assurance",
        "note": (
            "This endpoint reports configured names only, not live Pub/Sub API "
            "reachability (that requires IAM-scoped 'gcloud pubsub subscriptions "
            "describe' - see the read-only diagnostic commands in the implementation "
            "report). Auth for the push subscription is enforced at the Cloud Run IAM "
            "layer (--no-allow-unauthenticated + run.invoker for the push service "
            "account), not inside this application."
        ),
    }


def _worker_heartbeat_note() -> dict[str, Any]:
    return {
        "note": (
            "Worker heartbeat is tracked per-mission (metadata.last_heartbeat_at, "
            "stamped on execution lease acquisition), not as a single global worker "
            "liveness signal. Inspect an individual mission's metadata, or query "
            "recent RUNNING missions' last_heartbeat_at, to check whether the worker "
            "is actively processing."
        ),
    }


def _gemini_status() -> dict[str, Any]:
    """Reports the AI runtime configuration via GeminiDecisionClient.describe_runtime()
    - the SAME auth-mode resolution logic that a real mission's Gemini calls use - so
    this report can never drift from what actually executes. Never invokes Gemini,
    never validates credentials, never constructs a google.genai.Client, and never
    makes any network call; see GeminiDecisionClient.describe_runtime()'s docstring."""
    try:
        from app.agents.config import get_agent_config
        from app.agents.gemini_client import GeminiDecisionClient

        cfg = get_agent_config()
        client = GeminiDecisionClient(cfg)
        runtime = client.describe_runtime()
        return {
            **runtime,
            "endpoint_probe_invoked": False,
            "note": (
                "Configuration only, resolved via the same auth-mode logic GeminiDecisionClient "
                "uses for real calls. This endpoint never invokes Gemini, validates credentials, "
                "constructs a model client, or makes any network call."
            ),
        }
    except Exception as e:
        return {"error": str(e)}


def _bigquery_status(settings: Any) -> dict[str, Any]:
    return {
        "enabled": settings.bigquery_enabled,
        "dataset": settings.bigquery_dataset if settings.bigquery_enabled else None,
        "portfolio_table": settings.bigquery_portfolio_table if settings.bigquery_enabled else None,
        "note": "Configuration only; no query is executed by this endpoint.",
    }


def _artifact_storage_status(settings: Any) -> dict[str, Any]:
    import os

    return {
        "backend": os.getenv("RATEGUARD_ARTIFACT_STORE", "local"),
        "gcs_bucket_configured": bool(os.getenv("RATEGUARD_GCS_BUCKET")),
    }
