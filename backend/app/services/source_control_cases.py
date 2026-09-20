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


def control_cases_artifact_id(source_id: str) -> str:
    return f"CTRL-{source_id}"


def save_verified_control_cases(tenant_id: str, source_id: str, cases: list[dict], store=None) -> None:
    store = store or get_artifact_store()
    payload = json.dumps(cases, sort_keys=True, indent=2).encode("utf-8")
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
        return [ControlCase.model_validate(item) for item in json.loads(content)]
    except (ValueError, ValidationError) as exc:
        logger.error("Ignoring malformed control-case sidecar for source '%s': %s", source_id, type(exc).__name__)
        return []
