from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import ValidationError

from app.adapters.errors import SourceParsingError
from app.services.ingestion_service import PricingSourceIngestionService

router = APIRouter(prefix="/api/v1/sources", tags=["sources"])
ingestion_service = PricingSourceIngestionService()
_registered_sources: dict[str, Any] = {}


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
async def upload_pricing_source(file: UploadFile = File(...)) -> dict[str, Any]:
    """Uploads and registers a pricing source file (.json only today --
    Excel/PDF are not yet supported for verified, content-faithful
    extraction)."""
    try:
        content = await file.read()
        descriptor = ingestion_service.register_source(
            filename=file.filename or "uploaded_source",
            content_type=file.content_type or "application/octet-stream",
            content=content,
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
def compile_pricing_source(source_id: str) -> dict[str, Any]:
    """Compiles a registered source into a canonical IPIR package using its matching adapter."""
    desc = _registered_sources.get(source_id)
    if not desc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Registered source '{source_id}' not found.",
        )

    try:
        res = ingestion_service.compile_source(desc)
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
        return {
            "source_id": source_id,
            "adapter_id": res.adapter_id,
            "ipir_package_id": res.ipir_package.id,
            "mapping_coverage": res.mapping_coverage,
            "confidence": res.confidence,
            "warnings": res.warnings,
            "requires_human_review": res.requires_human_review,
            "compilation_receipt": receipt,
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
