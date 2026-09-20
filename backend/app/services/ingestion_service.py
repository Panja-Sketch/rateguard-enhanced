import hashlib
import uuid
from decimal import Decimal
from typing import Any

from app.adapters import AdapterResult, SourceDescriptor, SourceFormat
from app.adapters.errors import SourceParsingError
from app.agents.supervisor import AssuranceSupervisor
from app.ingestion.workbook_v1.compiler import compile_workbook
from app.ipir.enums import ProvenanceSourceType
from app.ipir.provenance import Provenance, SourceReference
from app.ipir.v0_2.compat import lower_to_v0_1
from app.services.source_control_cases import save_verified_control_cases
from app.storage import get_run_store
from app.storage.artifacts import (
    ArtifactCategory,
    ArtifactDescriptor,
    ArtifactKey,
    get_artifact_store,
)


class PricingSourceIngestionService:
    """Service orchestrating source upload, storage, compilation, and IPIR lineage.

    Compilation is delegated to `AssuranceSupervisor.extract_and_compile_source`
    rather than calling an adapter directly, so every source — regardless of
    whether a mission exists yet — goes through the same mandatory hash/
    provenance capture, extractor-selection (deterministic-first, Gemini only
    for genuinely ambiguous PDF sources), and IPIR schema validation.
    Controlled Workbook v1 ('.xlsx') sources are compiled separately, by the
    fully deterministic `app.ingestion.workbook_v1` compiler (see
    `_compile_controlled_workbook` below) rather than the Gemini-assisted
    pipeline.
    """

    def __init__(self) -> None:
        self.artifact_store = get_artifact_store()
        self.supervisor = AssuranceSupervisor(get_run_store())

    def register_source(
        self,
        filename: str,
        content_type: str,
        content: bytes,
        metadata: dict[str, Any] | None = None,
        *,
        tenant_id: str,
    ) -> SourceDescriptor:
        """Validates, stores, and registers a source file artifact under the
        caller's tenant prefix. `tenant_id` must come from the authenticated
        server context (never a request field) and is required: there is no
        tenantless default."""
        if len(content) > 20 * 1024 * 1024:
            raise SourceParsingError("File size exceeds maximum allowed 20MB limit.")

        ext = filename.split(".")[-1].lower()
        if ext == "json":
            fmt = SourceFormat.STRUCTURED_JSON
            cat = ArtifactCategory.SOURCE_JSON
        elif ext == "xlsx":
            # Routed to the Controlled RateGuard Workbook v1 compiler
            # (`app.ingestion.workbook_v1`) in `compile_source` below, which
            # performs full ZIP/XML safety inspection, sheet/formula
            # validation, and control-case execution before any IPIR package
            # is trusted. This is a genuinely constrained, verified contract
            # -- not arbitrary Excel interpretation.
            fmt = SourceFormat.EXCEL
            cat = ArtifactCategory.SOURCE_WORKBOOK
        elif ext in ("xls", "pdf"):
            # Legacy binary Excel (.xls) and PDF extraction are not
            # implemented: the legacy adapters for these formats parse (or,
            # for PDF, don't even parse) the uploaded bytes and then discard
            # them, substituting the bundled canonical demo IPIR package
            # regardless of actual content. That is fabrication, not
            # extraction -- a pricing-assurance tool must fail closed rather
            # than silently misrepresent an unverified extraction as a
            # genuine compilation. Rejected here until real, content-faithful
            # extraction exists and is proven end-to-end.
            raise SourceParsingError(
                f"'.{ext}' sources are not yet supported for verified extraction. "
                "RateGuard compiles native IPIR/structured JSON and the Controlled "
                "RateGuard Workbook v1 '.xlsx' contract today (see the sample "
                "template on the Sources page) -- legacy '.xls' and PDF support "
                "will be enabled once content-faithful extraction is implemented "
                "and proven, not before."
            )
        else:
            raise SourceParsingError(
                f"Unsupported file extension '.{ext}'. Allowed: .json, .xlsx"
            )

        source_id = f"SRC-{uuid.uuid4().hex[:8].upper()}"
        sha256_hash = hashlib.sha256(content).hexdigest()
        full_metadata = {**(metadata or {}), "sha256": sha256_hash}

        art_desc = ArtifactDescriptor(
            artifact_id=source_id,
            tenant_id=tenant_id,
            scope="sources",
            scope_id=source_id,
            kind="raw",
            category=cat,
            filename=filename,
            content_type=content_type or "application/octet-stream",
            size_bytes=len(content),
            storage_uri="",
            metadata=full_metadata,
        )
        saved_desc = self.artifact_store.save_artifact(art_desc, content)

        return SourceDescriptor(
            source_id=source_id,
            name=filename,
            source_type=fmt,
            format=ext,
            storage_uri=saved_desc.storage_uri,
            metadata=full_metadata,
        )

    def compile_source(self, source_descriptor: SourceDescriptor, *, tenant_id: str) -> AdapterResult:
        """Compiles registered source bytes to IPIR via the mandatory
        `AssuranceSupervisor.extract_and_compile_source` pipeline, then saves
        the compiled IPIR artifact."""
        raw_key = ArtifactKey(tenant_id, "sources", source_descriptor.source_id, "raw", source_descriptor.source_id)
        content = self.artifact_store.get_artifact_content(raw_key)
        if not content:
            raise SourceParsingError(
                f"Artifact content for source '{source_descriptor.source_id}' not found."
            )

        if source_descriptor.source_type == SourceFormat.EXCEL:
            # Controlled RateGuard Workbook v1: a fully deterministic,
            # security-inspected compiler (`app.ingestion.workbook_v1`), not
            # the legacy Gemini-assisted extraction-strategy pipeline used
            # for genuinely ambiguous PDF sources. Never falls through to
            # the fabricating legacy `ExcelPricingAdapter`.
            result = _compile_controlled_workbook(source_descriptor, content)
        else:
            result = self.supervisor.extract_and_compile_source(source_descriptor, content)

        # A package's `id` is declared by whatever the uploaded source itself
        # says it is (e.g. copy-pasted from a shared template) -- nothing
        # about it is guaranteed unique across different uploads. Two
        # different sources that both declare the same id would otherwise
        # collide, and one's evidence/artifacts could be mistaken for the
        # other's. Every compiled package's identity is made unique to its
        # own upload by namespacing it to the immutable source_id, while
        # keeping the original declared id readable as a prefix.
        original_package_id = result.ipir_package.id
        result.ipir_package.id = f"{original_package_id}--{source_descriptor.source_id}"

        # Save compiled IPIR artifact
        ipir_json = result.ipir_package.model_dump_json(indent=2).encode("utf-8")
        ipir_art = ArtifactDescriptor(
            artifact_id=f"IPIR-{source_descriptor.source_id}",
            tenant_id=tenant_id,
            scope="sources",
            scope_id=source_descriptor.source_id,
            kind="compiled",
            category=ArtifactCategory.IPIR_PACKAGE,
            filename=f"{result.ipir_package.id}.json",
            content_type="application/json",
            size_bytes=len(ipir_json),
            storage_uri="",
            metadata={"source_id": source_descriptor.source_id},
        )
        self.artifact_store.save_artifact(ipir_art, ipir_json)

        verified_cases = result.evidence.get("verified_control_cases")
        if verified_cases:
            save_verified_control_cases(tenant_id, source_descriptor.source_id, verified_cases, self.artifact_store)

        return result


def _compile_controlled_workbook(source: SourceDescriptor, content: bytes) -> AdapterResult:
    """Compiles a Controlled RateGuard Workbook v1 ('.xlsx') source via the
    fully deterministic `app.ingestion.workbook_v1.compile_workbook`
    pipeline (ZIP/XML safety inspection, sheet/formula validation, IPIR v0.2
    construction, and control-case execution against the existing oracle),
    then lowers the result to a v0.1 `IPIRPackage` so it fits the same
    `AdapterResult` contract every other source format returns.

    A `REJECTED` receipt (any security/structural violation) raises
    `SourceParsingError` immediately -- it never reaches the caller as a
    package. A `REVIEW_REQUIRED` receipt (e.g. missing/failing control
    cases, an ambiguous mapping) still returns an `AdapterResult`, but with
    reduced confidence and `requires_human_review=True`, so it can never be
    mistaken for a verified compilation downstream.
    """
    receipt = compile_workbook(content, source.name)

    if receipt.status == "REJECTED":
        reasons = "; ".join(f"{err.code}: {err.message}" for err in receipt.errors) or "unknown reason"
        raise SourceParsingError(
            f"Controlled Workbook v1 compilation rejected for '{source.name}': {reasons}"
        )

    assert receipt.package is not None  # guaranteed whenever status != REJECTED
    ipir_package = lower_to_v0_1(receipt.package)

    provenance = Provenance(
        sources=[
            SourceReference(
                source_type=ProvenanceSourceType.ACTUARIAL_WORKBOOK,
                source_id=source.source_id,
                source_name=source.name,
                section=f"Sheets: {', '.join(receipt.sheets_found)}",
                location=f"artifact_sha256={receipt.artifact_sha256}",
            )
        ],
        extraction_confidence=Decimal("1") if receipt.status == "VERIFIED" else Decimal("0.5"),
        interpretation_confidence=Decimal("1") if receipt.status == "VERIFIED" else Decimal("0.5"),
        notes=(
            f"Compiled via Controlled RateGuard Workbook v1 compiler "
            f"({receipt.compiler_version}); status={receipt.status}."
        ),
    )

    # Only control cases that actually passed against the oracle are handed on
    # to mission test planning (a failing golden example is not a valid probe).
    failed_case_ids = {r.case_id for r in receipt.control_case_results if not r.passed}
    verified_control_cases = [
        case.model_dump(mode="json")
        for case in receipt.package.control_cases
        if case.case_id not in failed_case_ids
        and any(r.case_id == case.case_id for r in receipt.control_case_results)
    ]

    return AdapterResult(
        source_id=source.source_id,
        source_type=SourceFormat.EXCEL,
        adapter_id="controlled_workbook_v1_compiler",
        ipir_package=ipir_package,
        mapping_coverage=100.0 if receipt.status == "VERIFIED" else 50.0,
        warnings=list(receipt.warnings),
        unsupported_constructs=list(receipt.rejected_constructs),
        confidence=1.0 if receipt.status == "VERIFIED" else 0.4,
        evidence={
            "compilation_receipt": receipt.model_dump(mode="json", exclude={"package"}),
            "verified_control_cases": verified_control_cases,
        },
        provenance=provenance,
        requires_human_review=receipt.status != "VERIFIED",
    )
