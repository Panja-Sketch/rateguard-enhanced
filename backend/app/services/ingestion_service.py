import hashlib
import uuid
from typing import Any

from app.adapters import AdapterResult, SourceDescriptor, SourceFormat
from app.adapters.errors import SourceParsingError
from app.agents.supervisor import AssuranceSupervisor
from app.storage import get_run_store
from app.storage.artifacts import ArtifactCategory, ArtifactDescriptor, get_artifact_store


class PricingSourceIngestionService:
    """Service orchestrating source upload, storage, compilation, and IPIR lineage.

    Compilation is delegated to `AssuranceSupervisor.extract_and_compile_source`
    rather than calling an adapter directly, so every source — regardless of
    whether a mission exists yet — goes through the same mandatory hash/
    provenance capture, extractor-selection (deterministic-first, Gemini only
    for genuinely ambiguous PDF/Excel sources), and IPIR schema validation.
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
    ) -> SourceDescriptor:
        """Validates, stores, and registers a source file artifact."""
        if len(content) > 20 * 1024 * 1024:
            raise SourceParsingError("File size exceeds maximum allowed 20MB limit.")

        ext = filename.split(".")[-1].lower()
        if ext == "json":
            fmt = SourceFormat.STRUCTURED_JSON
            cat = ArtifactCategory.SOURCE_JSON
        elif ext in ("xlsx", "xls", "pdf"):
            # Excel and PDF extraction is not implemented: the adapters for
            # these formats parse (or, for PDF, don't even parse) the
            # uploaded bytes and then discard them, substituting the bundled
            # canonical demo IPIR package regardless of actual content. That
            # is fabrication, not extraction -- a pricing-assurance tool must
            # fail closed rather than silently misrepresent an unverified
            # extraction as a genuine compilation. Rejected here until real,
            # content-faithful extraction exists and is proven end-to-end.
            raise SourceParsingError(
                f"'.{ext}' sources are not yet supported for verified extraction. "
                "RateGuard only compiles native IPIR/structured JSON sources today "
                "(see the sample template on the Sources page) -- Excel and PDF "
                "support will be enabled once content-faithful extraction is "
                "implemented and proven, not before."
            )
        else:
            raise SourceParsingError(
                f"Unsupported file extension '.{ext}'. Allowed: .json"
            )

        source_id = f"SRC-{uuid.uuid4().hex[:8].upper()}"
        sha256_hash = hashlib.sha256(content).hexdigest()
        full_metadata = {**(metadata or {}), "sha256": sha256_hash}

        art_desc = ArtifactDescriptor(
            artifact_id=source_id,
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

    def compile_source(self, source_descriptor: SourceDescriptor) -> AdapterResult:
        """Compiles registered source bytes to IPIR via the mandatory
        `AssuranceSupervisor.extract_and_compile_source` pipeline, then saves
        the compiled IPIR artifact."""
        content = self.artifact_store.get_artifact_content(source_descriptor.source_id)
        if not content:
            raise SourceParsingError(
                f"Artifact content for source '{source_descriptor.source_id}' not found."
            )

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
            category=ArtifactCategory.IPIR_PACKAGE,
            filename=f"{result.ipir_package.id}.json",
            content_type="application/json",
            size_bytes=len(ipir_json),
            storage_uri="",
            metadata={"source_id": source_descriptor.source_id},
        )
        self.artifact_store.save_artifact(ipir_art, ipir_json)

        return result
