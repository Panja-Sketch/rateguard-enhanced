"""Sidecar persistence of a compiled workbook's *verified* control cases.

Lowering a Controlled Workbook v1 (IPIR v0.2) package to the v0.1 package the
oracle and supervisor use discards the control cases. They are the workbook
author's own golden examples, so the mission's test planner needs them; this
stores exactly the cases that passed verification at compile time next to the
compiled IPIR, inside the same tenant-prefixed `compiled` artifact scope.
"""

from __future__ import annotations

import json
import logging

from pydantic import ValidationError

from app.ipir.v0_2.control_cases import ControlCase
from app.storage.artifacts import (
    ArtifactCategory,
    ArtifactDescriptor,
    ArtifactKey,
    get_artifact_store,
)

logger = logging.getLogger(__name__)


# Control-case sidecar artifact versions:
#   (none)  workbook compiled before control-case sidecars existed  -> REUPLOAD_REQUIRED
#   1       bare JSON list of cases (Prompt 7)                       -> readable, accepted
#   2       {"artifact_version": 2, "cases": [...]} (current)        -> accepted
CONTROL_CASE_ARTIFACT_VERSION = 2


def control_cases_artifact_id(source_id: str) -> str:
    return f"CTRL-{source_id}"


def save_verified_control_cases(tenant_id: str, source_id: str, cases: list[dict], store=None) -> None:
    store = store or get_artifact_store()
    payload = json.dumps(
        {"artifact_version": CONTROL_CASE_ARTIFACT_VERSION, "cases": cases}, sort_keys=True, indent=2
    ).encode("utf-8")
    store.save_artifact(
        ArtifactDescriptor(
            artifact_id=control_cases_artifact_id(source_id),
            tenant_id=tenant_id,
            scope="sources",
            scope_id=source_id,
            kind="compiled",
            category=ArtifactCategory.IPIR_PACKAGE,
            filename=f"{control_cases_artifact_id(source_id)}.json",
            content_type="application/json",
            size_bytes=len(payload),
            storage_uri="",
            metadata={"source_id": source_id, "content": "verified_control_cases"},
        ),
        payload,
    )


def load_verified_control_cases(tenant_id: str, source_id: str, store=None) -> list[ControlCase]:
    """Returns the source's verified control cases, or [] when none were stored
    (e.g. a JSON source, or a workbook compiled before this sidecar existed).
    A malformed sidecar is treated as absent and logged, never trusted."""
    store = store or get_artifact_store()
    content = store.get_artifact_content(
        ArtifactKey(tenant_id, "sources", source_id, "compiled", control_cases_artifact_id(source_id))
    )
    if not content:
        return []
    try:
        raw = json.loads(content)
        items = raw["cases"] if isinstance(raw, dict) else raw  # v2 wrapper | v1 bare list
        return [ControlCase.model_validate(item) for item in items]
    except (ValueError, ValidationError) as exc:
        logger.error("Ignoring malformed control-case sidecar for source '%s': %s", source_id, type(exc).__name__)
        return []


def is_controlled_workbook(tenant_id: str, source_id: str, store=None) -> bool:
    """True when the source's original upload was an Excel workbook (the only
    source type that carries workbook control cases)."""
    store = store or get_artifact_store()
    try:
        desc = store.get_descriptor(ArtifactKey(tenant_id, "sources", source_id, "raw", source_id))
    except Exception:  # noqa: BLE001 - absence/path errors mean "not a workbook we can vouch for"
        return False
    name = (getattr(desc, "filename", "") or "").lower()
    return name.endswith((".xlsx", ".xlsm"))


def control_case_compatibility(tenant_id: str, source_id: str, store=None) -> dict:
    """Compatibility state of a compiled source's control-case artifact.

    A workbook compiled before the control-case artifact existed is never given
    fabricated cases: it is reported `REUPLOAD_REQUIRED` and must be re-uploaded
    and re-compiled. Existing evidence for missions that used it stays readable."""
    store = store or get_artifact_store()
    if not is_controlled_workbook(tenant_id, source_id, store):
        return {"state": "NOT_APPLICABLE", "artifact_version": None, "message": None}
    content = store.get_artifact_content(
        ArtifactKey(tenant_id, "sources", source_id, "compiled", control_cases_artifact_id(source_id))
    )
    if not content:
        return {
            "state": "REUPLOAD_REQUIRED",
            "artifact_version": None,
            "message": (
                "This workbook was compiled before control-case artifacts (compiler artifact v"
                f"{CONTROL_CASE_ARTIFACT_VERSION}) existed, so its workbook control cases are unavailable. "
                "Re-upload the .xlsx and compile it again to enable control-case probing; no cases are inferred."
            ),
        }
    try:
        raw = json.loads(content)
    except ValueError:
        return {"state": "REUPLOAD_REQUIRED", "artifact_version": None,
                "message": "The stored control-case artifact is unreadable; re-upload and recompile the workbook."}
    version = raw.get("artifact_version", 2) if isinstance(raw, dict) else 1
    return {"state": "OK", "artifact_version": version, "message": None}
