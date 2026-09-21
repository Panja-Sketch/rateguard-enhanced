"""Tenant-scoped, deterministic evidence bundle (`evidence-bundle-v1`).

A ZIP of stable JSON files plus a human-readable summary, with a manifest that
carries the SHA-256 of every file. The manifest's own hash is reported
separately (response header and `manifest.sha256` inside the archive).

Exclusions are enforced structurally: every section is built from explicit
allowlists (never by passing stored records through), then the serialized
output is scanned; a secret-shaped value or a forbidden key aborts the export
(fail closed) instead of being downloaded.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import zipfile
from datetime import UTC, datetime
from typing import Any

BUNDLE_SCHEMA_VERSION = "evidence-bundle-v1"
_ZIP_EPOCH = (2020, 1, 1, 0, 0, 0)

# Keys that must never appear anywhere in a bundle.
FORBIDDEN_KEY_PATTERN = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|private[_-]?key|authorization|credential|base_url|"
    r"auth_header|auth_token|email|phone|address|policy_id|policyholder|ssn|account_number)",
    re.IGNORECASE,
)
# Benign counters whose names merely contain a forbidden fragment.
ALLOWED_KEYS = frozenset({"input_tokens", "output_tokens"})
# Value shapes that indicate a leaked secret / PII.
SECRET_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),
    re.compile(r"\bya29\.[0-9A-Za-z_\-]{20,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9_\-\.=]{20,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),  # e-mail address
)


class EvidenceBundleError(RuntimeError):
    """Raised when a bundle cannot be produced safely (never carries content)."""


def stable_json(obj: Any) -> bytes:
    return (json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False, default=str) + "\n").encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _walk(obj: Any, path: str = ""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield f"{path}/{k}", k, None
            yield from _walk(v, f"{path}/{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        yield path, None, obj


def scan_for_forbidden(name: str, obj: Any) -> None:
    for _path, key, value in _walk(obj):
        if key is not None and key not in ALLOWED_KEYS and FORBIDDEN_KEY_PATTERN.search(str(key)):
            raise EvidenceBundleError(f"forbidden key in section '{name}'")
        if value is not None:
            for pattern in SECRET_VALUE_PATTERNS:
                if pattern.search(value):
                    raise EvidenceBundleError(f"secret- or PII-shaped value in section '{name}'")


def _pick(src: dict[str, Any] | None, keys: tuple[str, ...]) -> dict[str, Any]:
    src = src or {}
    return {k: src.get(k) for k in keys if k in src}


_GEMINI_FIELDS = (
    "decision_type", "model_id", "invocation_id", "response_id", "schema_valid", "success",
    "failure_category", "requested_tool", "started_at", "ended_at", "latency_ms", "input_tokens", "output_tokens",
)
_CONNECTOR_EVIDENCE_FIELDS = (
    "connector_id", "engine_version", "correlation_id", "connector_request_id", "request_sha256",
    "response_sha256", "status", "final_premium", "error_code", "scenario_id", "probe_origin",
    "calculation_date", "calculation_date_source",
)
_EXPERIMENT_FIELDS = (
    "experiment_id", "probe_name", "category", "risk_inputs", "expected_premium", "actual_premium", "matches",
    "outcome", "inconclusive_reason", "calculation_date", "calculation_date_source", "probe_origin",
)
_EXPLANATION_FIELDS = (
    "explanation_id", "status", "source", "validated", "facts_sha256", "created_at", "reviewed_at",
    "rejection_reason",
)


def normalize_reason_codes(obj: Any) -> Any:
    """Compatibility mapping for evidence stored before the doubled-prefix fix
    (`CONNECTOR_CONNECTOR_FAILURE` -> `CONNECTOR_FAILURE`). Stored data is never
    rewritten; it is normalised on read."""
    if isinstance(obj, str):
        return obj.replace("CONNECTOR_CONNECTOR_", "CONNECTOR_")
    if isinstance(obj, list):
        return [normalize_reason_codes(v) for v in obj]
    if isinstance(obj, dict):
        return {k: normalize_reason_codes(v) for k, v in obj.items()}
    return obj


def build_sections(
    *, record: Any, tenant_id: str, report: dict[str, Any], gemini_evidence: list[dict[str, Any]],
    connector_evidence: list[dict[str, Any]], explanations: list[dict[str, Any]],
    connector_meta: dict[str, Any] | None, impact_job: dict[str, Any] | None,
) -> dict[str, Any]:
    report = normalize_reason_codes(report or {})
    meta = record.metadata if isinstance(record.metadata, dict) else {}
    decision = ((report.get("release_decision") or {}).get("data")) or {}
    impact = ((report.get("connector_impact") or {}).get("data")) or (impact_job or {}).get("aggregate")
    experiments = (((report.get("experiments") or {}).get("data")) or {}).get("experiments", [])
    mission_obj = (meta.get("mission_object") or {}) if isinstance(meta.get("mission_object"), dict) else {}
    src_a = mission_obj.get("source_a") or {}
    src_b = mission_obj.get("source_b") or {}

    limitations: list[str] = []
    if impact:
        if impact.get("completeness") != "COMPLETE":
            limitations.append(f"Connector portfolio impact is {impact.get('status')}: results are partial; "
                               "exposure figures are lower bounds when a mismatch was proven.")
        if impact.get("out_of_scope_policies"):
            limitations.append(
                f"{impact['out_of_scope_policies']} policies were out of scope for the authoritative source "
                f"({impact.get('out_of_scope_reasons')}) and were not repriced.")
        if impact.get("inconclusive"):
            limitations.append(f"{impact['inconclusive']} policies could not be compared (inconclusive).")
    elif (report.get("connector_impact") or {}).get("reason"):
        limitations.append("Connector portfolio impact was not run: " + str(report["connector_impact"]["reason"]))
    limitations.append("Portfolio data is synthetic/de-identified; results are not a legal or actuarial finding.")

    return {
        "mission_summary.json": {
            "mission_id": record.run_id, "tenant_id": tenant_id, "name": mission_obj.get("name"),
            "mode": mission_obj.get("mode"), "status": getattr(record.status, "value", str(record.status)),
            "decision": record.decision, "attempt_number": record.attempt_number,
            "created_at": str(record.created_at), "completed_at": str(record.completed_at or ""),
            "overall_status": report.get("overall_status"),
        },
        "inputs.json": {
            "source_a": _pick(src_a, ("source_id", "source_type", "name", "format", "hash_checksum", "compiled_package_id")),
            "source_b": _pick(src_b, ("source_id", "source_type", "name", "connector_id", "engine_version")),
            "portfolio_snapshot": (impact or {}).get("provenance", {}).get("portfolio_snapshot"),
            "authoritative_source_identity": (impact or {}).get("provenance", {}).get("source"),
            "calculation_date_basis": (impact or {}).get("provenance", {}).get("calculation_date_basis"),
            "impact_as_of": (impact or {}).get("provenance", {}).get("as_of"),
            "impact_job_id": (impact or {}).get("job_id"),
        },
        "connector.json": {
            "registry": connector_meta,
            "connector_revisions": (impact or {}).get("provenance", {}).get("connector_revisions"),
            "batch_quote_used": (impact or {}).get("provenance", {}).get("batch_quote_used"),
            "invocations": connector_evidence,
        },
        "probes.json": {
            "experiments": [_pick(e, _EXPERIMENT_FIELDS) for e in experiments],
            "summary": _pick(((report.get("experiments") or {}).get("data")) or {},
                             ("total_executed", "match_count", "mismatch_count", "inconclusive_count")),
        },
        "impact.json": impact or {"status": "NOT_RUN", "reason": (report.get("connector_impact") or {}).get("reason")},
        "explanations.json": {"explanations": [_pick(e, _EXPLANATION_FIELDS) for e in explanations]},
        "model_invocations.json": {"invocations": [_pick(g, _GEMINI_FIELDS + ("evidence_id",)) for g in gemini_evidence]},
        "decision.json": {
            "decision": decision.get("status") or record.decision,
            "rationale": decision.get("summary"),
            "blocking_reasons": decision.get("blocking_reasons", []),
            "recommendation": decision.get("recommendation"),
            "stage_outcomes": report.get("stage_outcomes", []),
        },
        "limitations.json": {"limitations": limitations},
        "deployment.json": {
            "git_sha": os.environ.get("RATEGUARD_GIT_SHA"),
            "image_digest": os.environ.get("RATEGUARD_IMAGE_DIGEST"),
            "cloud_run_service": os.environ.get("K_SERVICE"),
            "cloud_run_revision": os.environ.get("K_REVISION"),
        },
    }


def render_summary(sections: dict[str, Any]) -> str:
    ms, dec, imp = sections["mission_summary.json"], sections["decision.json"], sections["impact.json"]
    lines = [
        f"# Evidence summary - {ms['mission_id']}", "",
        f"- Decision: **{dec['decision']}**", f"- Status: {ms['status']}",
        f"- Rationale: {dec.get('rationale') or 'n/a'}", "",
    ]
    if imp.get("status") and imp.get("status") != "NOT_RUN":
        lines += [
            "## Connector portfolio impact", f"- Status: {imp['status']} ({imp.get('completeness')})",
            f"- Compared: {imp.get('successful_comparisons')} of {imp.get('eligible_policies')} eligible "
            f"({imp.get('coverage_pct')}% coverage)",
            f"- Mismatches: {imp.get('mismatches')}; inconclusive: {imp.get('inconclusive')}",
            f"- Absolute exposure: ${imp.get('absolute_exposure')}"
            + (" (LOWER BOUND - scan incomplete)" if imp.get("exposure_is_lower_bound") else ""),
            f"- Signed net delta: ${imp.get('signed_net_delta')}", "",
        ]
    lines += ["## Limitations", *[f"- {x}" for x in sections["limitations.json"]["limitations"]], ""]
    return "\n".join(lines)


def build_bundle(sections: dict[str, Any], *, mission_id: str, tenant_id: str, exported_at: datetime | None = None
                 ) -> tuple[bytes, dict[str, Any], str]:
    """Returns (zip_bytes, manifest, manifest_sha256). Raises `EvidenceBundleError`
    if any section carries a forbidden key or secret-/PII-shaped value."""
    files: dict[str, bytes] = {}
    for name, obj in sections.items():
        scan_for_forbidden(name, obj)
        files[name] = stable_json(obj)
    summary = render_summary(sections)
    for pattern in SECRET_VALUE_PATTERNS:
        if pattern.search(summary):
            raise EvidenceBundleError("secret- or PII-shaped value in summary")
    files["summary.md"] = summary.encode("utf-8")

    manifest = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "mission_id": mission_id,
        "tenant_id": tenant_id,
        "exported_at": (exported_at or datetime.now(UTC)).isoformat(),
        "files": [{"path": n, "sha256": sha256_hex(files[n]), "bytes": len(files[n])} for n in sorted(files)],
        "exclusions": [
            "credentials, tokens and secret values", "connector base URLs and auth configuration",
            "raw policyholder PII and complete portfolio rows", "unrestricted logs and raw model prompts/responses",
        ],
    }
    manifest_bytes = stable_json(manifest)
    manifest_sha = sha256_hex(manifest_bytes)
    files["manifest.json"] = manifest_bytes
    files["manifest.sha256"] = (manifest_sha + "  manifest.json\n").encode("utf-8")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=_ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, files[name])
    return buf.getvalue(), manifest, manifest_sha
