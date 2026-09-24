import hashlib
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from pydantic import ValidationError

from app.adapters.errors import SourceParsingError
from app.auth import AuthenticatedUser, require_read, require_roles
from app.auth.dependencies import RELEASE_WRITE_ROLES
from app.auth.tenancy import is_visible_to, not_found
from app.ratelimit import rate_limited
from app.services.ingestion_service import PricingSourceIngestionService
from app.storage.artifacts import ArtifactKey, ArtifactPathError, get_artifact_store

router = APIRouter(prefix="/api/v1/sources", tags=["sources"])
ingestion_service = PricingSourceIngestionService()
_registered_sources: dict[str, Any] = {}

# Raw/compiled source artifacts may be downloaded only by roles that may author
# releases (locked doc 13.1: "authorized signed download"); read-only and
# consumer-review roles never get source bytes.
require_source_write = require_roles(*RELEASE_WRITE_ROLES)
require_source_download = require_roles(*RELEASE_WRITE_ROLES)

_UPLOADED_SOURCE_PREFIX = "SRC-"


def _artifact_key(user: AuthenticatedUser, source_id: str, artifact_id: str) -> ArtifactKey | None:
    """Tenant-prefixed key for a source's raw or compiled artifact, or None when
    the ids are unsafe/unknown. The tenant is the caller's server-side tenant."""
    if artifact_id == source_id:
        kind = "raw"
    elif artifact_id == f"IPIR-{source_id}":
        kind = "compiled"
    else:
        return None
    try:
        return ArtifactKey(user.tenant_id, "sources", source_id, kind, artifact_id)
    except ArtifactPathError:
        return None


def source_accessible(source_id: str, user: AuthenticatedUser) -> bool:
    """Bundled demo package ids and registered connector ids are shared read-only
    fixtures. An uploaded source (`SRC-…`) is accessible only if its raw artifact
    exists under the caller's OWN tenant prefix — ownership is structural, so it
    holds across API/worker instances and cannot be forged with another tenant's id."""
    if not source_id.startswith(_UPLOADED_SOURCE_PREFIX):
        return True
    key = _artifact_key(user, source_id, source_id)
    return key is not None and get_artifact_store().exists(key)


def uploaded_source_sha256(source_id: str, user: AuthenticatedUser) -> str | None:
    """SHA-256 of an uploaded source's raw bytes, read from the caller's OWN tenant
    artifact prefix. Server-derived so mission evidence records the real content
    hash of the controlled workbook and never a browser-supplied value; None for
    bundled fixtures, connectors or anything not uploaded by this tenant."""
    if not source_id.startswith(_UPLOADED_SOURCE_PREFIX):
        return None
    key = _artifact_key(user, source_id, source_id)
    if key is None:
        return None
    content = get_artifact_store().get_artifact_content(key)
    return hashlib.sha256(content).hexdigest() if content is not None else None


def _validation_error_detail(exc: ValidationError) -> dict[str, Any]:
    """Turns a raw Pydantic ValidationError into the same structured,
    actionable shape mission validation already uses (field/code/message per
    issue) instead of a raw stack-trace-shaped dump."""
    issues = []
    for err in exc.errors():
        field = ".".join(str(p) for p in err.get("loc", ())) or "(root)"
        issues.append({
            "field": field,
            "code": err.get("type", "INVALID"),
            "message": err.get("msg", "Invalid value."),
        })
    return {"message": "Source schema validation failed.", "issues": issues}


@router.post("")
async def upload_pricing_source(
    file: UploadFile = File(...),
    user: AuthenticatedUser = Depends(require_source_write),
    _quota: None = Depends(rate_limited("source_upload")),
) -> dict[str, Any]:
    """Uploads and registers a pricing source file (.json only today --
    Excel/PDF are not yet supported for verified, content-faithful
    extraction)."""
    try:
        content = await file.read()
        descriptor = ingestion_service.register_source(
            filename=file.filename or "uploaded_source",
            content_type=file.content_type or "application/octet-stream",
            content=content,
            metadata={"tenant_id": user.tenant_id, "uploaded_by": user.uid},
            tenant_id=user.tenant_id,
        )
        _registered_sources[descriptor.source_id] = descriptor
        return {
            "source_id": descriptor.source_id,
            "name": descriptor.name,
            "source_type": descriptor.source_type.value,
            "format": descriptor.format,
            "storage_uri": descriptor.storage_uri,
            "status": "REGISTERED",
        }
    except SourceParsingError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload pricing source: {e}",
        ) from e


@router.post("/{source_id}/compile")
def compile_pricing_source(
    source_id: str,
    user: AuthenticatedUser = Depends(require_source_write),
    _quota: None = Depends(rate_limited("source_compile")),
) -> dict[str, Any]:
    """Compiles a registered source into a canonical IPIR package using its matching adapter."""
    desc = _registered_sources.get(source_id)
    if desc is not None and not is_visible_to((desc.metadata or {}).get("tenant_id"), user):
        desc = None
    if not desc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Registered source '{source_id}' not found.",
        )

    try:
        res = ingestion_service.compile_source(desc, tenant_id=user.tenant_id)
        pkg = res.ipir_package
        # Compilation receipt: the concrete, auditable evidence of what was
        # actually parsed out of the uploaded source, so a user never has to
        # trust a bare confidence percentage on faith. Every count here is
        # read directly off the compiled package, never estimated.
        receipt = {
            "product": pkg.product.name,
            "product_line": pkg.product.line.value if hasattr(pkg.product.line, "value") else str(pkg.product.line),
            "jurisdiction": pkg.product.jurisdiction.state_or_province or pkg.product.jurisdiction.country,
            "effective_period_start": str(pkg.effective_period.start),
            "effective_period_end": str(pkg.effective_period.end) if pkg.effective_period.end else None,
            "input_count": len(pkg.inputs),
            "constant_count": len(pkg.constants),
            "table_count": len(pkg.tables),
            "table_row_count": sum(len(t.rows) for t in pkg.tables),
            "rule_count": len(pkg.rules),
            "calculation_count": len(pkg.calculations),
            "output_count": len(pkg.outputs),
            "output_node_ids": [o.id for o in pkg.outputs],
        }
        # The Controlled Workbook v1 compiler (app.ingestion.workbook_v1) computes
        # its own, much richer CompilationReceipt (compiler_version,
        # artifact_sha256, status, control_case_results, errors, warnings) and
        # stores it under res.evidence["compilation_receipt"] -- surfaced here
        # under its own key, additive to (never replacing) the generic
        # `compilation_receipt` summary above, which every source type gets.
        workbook_compilation_receipt = (
            res.evidence.get("compilation_receipt")
            if res.adapter_id == "controlled_workbook_v1_compiler"
            else None
        )
        return {
            "source_id": source_id,
            "adapter_id": res.adapter_id,
            "ipir_package_id": res.ipir_package.id,
            "mapping_coverage": res.mapping_coverage,
            "confidence": res.confidence,
            "warnings": res.warnings,
            "requires_human_review": res.requires_human_review,
            "compilation_receipt": receipt,
            "workbook_compilation_receipt": workbook_compilation_receipt,
            "ipir_package": res.ipir_package.model_dump(mode="json"),
        }
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_validation_error_detail(e),
        ) from e
    except SourceParsingError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Source compilation failed: {e}",
        ) from e


@router.get("/{source_id}")
def get_pricing_source(source_id: str, user: AuthenticatedUser = Depends(require_read)) -> dict[str, Any]:
    """Safe source metadata within the caller's tenant. Never returns the
    storage URI or any artifact bytes."""
    desc = _registered_sources.get(source_id)
    if desc is None or not is_visible_to((desc.metadata or {}).get("tenant_id"), user):
        raise not_found("Registered source", source_id)
    return {
        "source_id": desc.source_id,
        "name": desc.name,
        "source_type": desc.source_type.value,
        "format": desc.format,
        "sha256": (desc.metadata or {}).get("sha256"),
        "tenant_id": (desc.metadata or {}).get("tenant_id"),
        "artifact_ids": [desc.source_id, f"IPIR-{desc.source_id}"],
        "compatibility": _source_compatibility(desc, user),
    }


@router.get("/{source_id}/artifacts/{artifact_id}")
def download_source_artifact(
    source_id: str,
    artifact_id: str,
    user: AuthenticatedUser = Depends(require_source_download),
    _quota: None = Depends(rate_limited("source_download")),
) -> Response:
    """Authorized, tenant-scoped download of a source's raw or compiled artifact."""
    desc = _registered_sources.get(source_id)
    if desc is None or not is_visible_to((desc.metadata or {}).get("tenant_id"), user):
        raise not_found("Registered source", source_id)
    key = _artifact_key(user, source_id, artifact_id)
    if key is None:
        raise not_found("Artifact", artifact_id)
    # Tenant ownership is enforced by the key itself: the object is looked up
    # under the caller's tenant prefix, so another tenant's artifact is absent.
    store = get_artifact_store()
    content = store.get_artifact_content(key)
    art = store.get_descriptor(key)
    if content is None or art is None:
        raise not_found("Artifact", artifact_id)
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{artifact_id}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


def _source_compatibility(desc, user) -> dict[str, Any]:
    """`REUPLOAD_REQUIRED` for a workbook compiled before the control-case
    artifact existed; `NOT_APPLICABLE`/`OK` otherwise. Never fabricates cases."""
    from app.services.source_control_cases import control_case_compatibility

    try:
        return control_case_compatibility(user.tenant_id, desc.source_id)
    except Exception:  # noqa: BLE001 - metadata read must never fail because of compatibility probing
        return {"state": "UNKNOWN", "artifact_version": None, "message": None}
