"""Consumer-explanation human review (locked doc 13.4, 10.2).

* `POST /missions/{mission_id}/explanations` snapshots the mission's own
  deterministic-facts draft into a reviewable record. The request has no body:
  draft text and facts come *only* from the stored mission result, never from
  the caller, so a client cannot inject explanation content.
* `POST /explanations/{id}/approve|reject` record the reviewer identity and
  timestamp. Drafts are never sent anywhere (locked doc 2.2).

Every record carries the mission's tenant; lookups outside the caller's tenant
return the same 404 as a missing record.
"""

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.auth import (
    AuthenticatedUser,
    require_explanation_create,
    require_explanation_review,
    require_read,
)
from app.auth.tenancy import get_scoped_run, include_legacy_for, not_found
from app.ratelimit import rate_limited
from app.storage import get_run_store

router = APIRouter(prefix="/api/v1", tags=["explanations"])

EXPLANATION_LABEL = "For authorized review—not sent."


class RejectExplanationRequest(BaseModel):
    """The only client-supplied field on the review endpoints. Role, tenant and
    reviewer identity are never accepted from the body (extra fields → 422)."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=1000)


def _mission_id_from_explanation_id(explanation_id: str) -> str | None:
    # Format: EXP-<8 hex>-<mission id>; the mission id is therefore derivable
    # without a cross-collection index.
    parts = explanation_id.split("-", 2)
    if len(parts) != 3 or parts[0] != "EXP" or not parts[2]:
        return None
    return parts[2]


def _public(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "explanation_id": record["explanation_id"],
        "mission_id": record["mission_id"],
        "status": record["status"],
        "label": EXPLANATION_LABEL,
        "draft_text": record["draft_text"],
        "source": record["source"],
        "validated": record["validated"],
        "facts": record["facts"],
        "facts_sha256": record["facts_sha256"],
        "created_at": record["created_at"],
        "reviewed_by": record.get("reviewed_by"),
        "reviewed_at": record.get("reviewed_at"),
        "rejection_reason": record.get("rejection_reason"),
    }


def _get_scoped_explanation(explanation_id: str, user: AuthenticatedUser) -> tuple[Any, dict[str, Any]]:
    mission_id = _mission_id_from_explanation_id(explanation_id)
    store = get_run_store()
    if mission_id is None:
        raise not_found("Explanation", explanation_id)
    include_legacy = include_legacy_for(user)
    run = store.get_run_for_tenant(mission_id, user.tenant_id, include_legacy=include_legacy)
    record = store.get_explanation(mission_id, explanation_id) if run is not None else None
    # The explanation's tenant is the mission's tenant; both are checked so a
    # forged record can never widen access.
    if run is None or record is None or (record.get("tenant_id") and record["tenant_id"] != user.tenant_id):
        raise not_found("Explanation", explanation_id)
    return store, record


@router.post("/missions/{mission_id}/explanations", status_code=status.HTTP_201_CREATED)
def create_explanation(
    mission_id: str,
    user: AuthenticatedUser = Depends(require_explanation_create),
    _quota: None = Depends(rate_limited("explanation_create")),
) -> dict[str, Any]:
    store = get_run_store()
    run = get_scoped_run(store, mission_id, user)

    report = run.report if isinstance(run.report, dict) else {}
    facts = ((report.get("explanation_facts") or {}).get("data")) or None
    draft = ((report.get("explanation_draft") or {}).get("data")) or None
    if not facts or not draft:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "NO_EXPLANATION_AVAILABLE",
                "message": "This mission produced no explanation draft (no reproduced premium mismatch).",
            },
        )

    # Idempotent per facts hash: re-requesting returns the existing record.
    for existing in store.list_explanations(mission_id, user.tenant_id):
        if existing.get("facts_sha256") == facts.get("facts_sha256"):
            return _public(existing)

    explanation_id = f"EXP-{uuid.uuid4().hex[:8].upper()}-{mission_id}"
    record = {
        "explanation_id": explanation_id,
        "mission_id": mission_id,
        "tenant_id": run.tenant_id or user.tenant_id,
        "created_by": user.uid,
        "created_at": datetime.now(UTC).isoformat(),
        "status": "DRAFT",
        "draft_text": draft.get("draft_text", ""),
        "source": draft.get("source", "deterministic_fallback"),
        "validated": bool(draft.get("validated", False)),
        "facts": facts,
        "facts_sha256": facts.get("facts_sha256", ""),
    }
    store.save_explanation(mission_id, record)
    return _public(record)


@router.get("/missions/{mission_id}/explanations")
def list_explanations(mission_id: str, user: AuthenticatedUser = Depends(require_read)) -> dict[str, Any]:
    store = get_run_store()
    get_scoped_run(store, mission_id, user)
    items = [_public(r) for r in store.list_explanations(mission_id, user.tenant_id)]
    return {"mission_id": mission_id, "count": len(items), "explanations": items}


def _review(
    explanation_id: str,
    user: AuthenticatedUser,
    decision: Literal["APPROVED", "REJECTED"],
    reason: str | None,
) -> dict[str, Any]:
    store, record = _get_scoped_explanation(explanation_id, user)
    if record["status"] != "DRAFT":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "EXPLANATION_ALREADY_REVIEWED",
                "message": f"Explanation is already {record['status']}.",
            },
        )
    if decision == "APPROVED" and not record.get("validated"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "EXPLANATION_NOT_VALIDATED", "message": "An unvalidated draft cannot be approved."},
        )
    record["status"] = decision
    record["reviewed_by"] = user.uid
    record["reviewed_at"] = datetime.now(UTC).isoformat()
    if reason:
        record["rejection_reason"] = reason
    store.save_explanation(record["mission_id"], record)
    store.log_event(
        run_id=record["mission_id"],
        stage="EXPLANATION_REVIEW",
        message=f"Explanation {decision.lower()} by reviewer.",
        agent_name="HumanReviewer",
        details={"explanation_id": explanation_id, "decision": decision, "reviewer_uid": user.uid},
    )
    return _public(record)


@router.post("/explanations/{explanation_id}/approve")
def approve_explanation(
    explanation_id: str, user: AuthenticatedUser = Depends(require_explanation_review)
) -> dict[str, Any]:
    return _review(explanation_id, user, "APPROVED", None)


@router.post("/explanations/{explanation_id}/reject")
def reject_explanation(
    explanation_id: str,
    payload: RejectExplanationRequest,
    user: AuthenticatedUser = Depends(require_explanation_review),
) -> dict[str, Any]:
    return _review(explanation_id, user, "REJECTED", payload.reason)
