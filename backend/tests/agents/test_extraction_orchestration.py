"""Tests for AssuranceSupervisor.extract_and_compile_source — the mandatory,
bounded source-extraction entry point. Covers the CHOOSE_EXTRACTION_STRATEGY
decision policy: deterministic-first routing for JSON/platform-config/
recognized-Excel, and a real (fake-injected) Gemini decision only for
genuinely ambiguous PDF/Excel sources.

Every test injects a FakeGeminiClient — no test reaches a real model or GCP.
"""

import io
from datetime import UTC, datetime
from pathlib import Path

import openpyxl
import pytest

from app.adapters.extractor_registry import EXTRACTOR_REGISTRY, is_known_extractor
from app.adapters.models import SourceDescriptor, SourceFormat
from app.agents.gemini_client import GeminiInvocationEvidence
from app.agents.supervisor import AssuranceSupervisor
from app.storage.memory_store import InMemoryRunStore

_DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data"


class FixedExtractorGeminiClient:
    """Fake client that always requests a fixed extractor id (or an invented
    one, to test rejection)."""

    def __init__(self, requested_tool: str, needs_human_review: bool = False):
        self.requested_tool = requested_tool
        self.needs_human_review = needs_human_review
        self.calls: list[str] = []

    def decide(self, decision_type, schema, system_instruction, prompt):
        self.calls.append(decision_type)
        now = datetime.now(UTC).isoformat()
        decision = schema.model_validate({
            "rationale": "Fake rationale for extraction-strategy test.",
            "confidence": 0.85,
            "needs_human_review": self.needs_human_review,
            "requested_tool": self.requested_tool,
        })
        evidence = GeminiInvocationEvidence(
            invocation_id="GEM-FAKE-EXTRACT",
            model_id="gemini-3.7-flash",
            decision_type=decision_type,
            started_at=now,
            ended_at=now,
            latency_ms=3.0,
            success=True,
            schema_valid=True,
            rationale=decision.rationale,
            confidence=decision.confidence,
            needs_human_review=decision.needs_human_review,
            requested_tool=decision.requested_tool,
        )
        return decision, evidence


class AlwaysFailGeminiClient:
    def __init__(self):
        self.calls: list[str] = []

    def decide(self, decision_type, schema, system_instruction, prompt):
        self.calls.append(decision_type)
        now = datetime.now(UTC).isoformat()
        evidence = GeminiInvocationEvidence(
            invocation_id="GEM-FAKE-FAIL",
            model_id="gemini-3.7-flash",
            decision_type=decision_type,
            started_at=now,
            ended_at=now,
            latency_ms=1.0,
            success=False,
            failure_category="TIMEOUT",
        )
        return None, evidence


def _supervisor(gemini_client) -> AssuranceSupervisor:
    return AssuranceSupervisor(InMemoryRunStore(), gemini_client=gemini_client)


def _make_ambiguous_excel_bytes() -> bytes:
    wb = openpyxl.Workbook()
    wb.active.title = "Sheet1"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_valid_ipir_json_uses_deterministic_parser_no_gemini():
    """Bullet 1: already-valid IPIR JSON must never consult Gemini."""
    fake = AlwaysFailGeminiClient()
    sup = _supervisor(fake)
    content = (_DATA_DIR / "actuarial" / "AZ_HO3_2026_09_rate_spec.json").read_bytes()
    desc = SourceDescriptor(
        source_id="SRC-JSON-01", name="spec.json", source_type=SourceFormat.STRUCTURED_JSON,
        format="json", storage_uri="x",
    )
    result = sup.extract_and_compile_source(desc, content)

    assert result.evidence["selected_extractor"] == "structured_json_direct_parser"
    assert result.evidence["selection_kind"] == "DETERMINISTIC"
    assert fake.calls == [], "Gemini must never be consulted to parse already-valid IPIR JSON"
    assert result.confidence == 1.0
    assert result.requires_human_review is False


def test_recognized_platform_config_json_deterministic_no_gemini():
    """Bullet 2: a known structured JSON shape (platform-config wrapper) is
    routed deterministically even when uploaded generically as STRUCTURED_JSON."""
    fake = AlwaysFailGeminiClient()
    sup = _supervisor(fake)
    content = (_DATA_DIR / "implementations" / "platform_config" / "AZ_HO3_2026_09_platform_config.json").read_bytes()
    desc = SourceDescriptor(
        source_id="SRC-JSON-02", name="cfg.json", source_type=SourceFormat.STRUCTURED_JSON,
        format="json", storage_uri="x",
    )
    result = sup.extract_and_compile_source(desc, content)
    assert result.evidence["selected_extractor"] == "platform_config_adapter"
    assert result.evidence["selection_kind"] == "DETERMINISTIC"
    assert fake.calls == []


def test_recognized_excel_layout_deterministic_no_gemini():
    """Bullet 3: a recognized actuarial workbook layout is extracted
    deterministically first, without consulting Gemini."""
    fake = AlwaysFailGeminiClient()
    sup = _supervisor(fake)
    content = (_DATA_DIR / "actuarial" / "AZ_HO3_2026_09_rate_spec.xlsx").read_bytes()
    desc = SourceDescriptor(
        source_id="SRC-XLS-01", name="spec.xlsx", source_type=SourceFormat.EXCEL,
        format="xlsx", storage_uri="x",
    )
    result = sup.extract_and_compile_source(desc, content)
    assert result.evidence["selected_extractor"] == "excel_named_range_extractor"
    assert result.evidence["selection_kind"] == "DETERMINISTIC"
    assert fake.calls == []


def test_ambiguous_pdf_triggers_real_gemini_decision_that_changes_the_branch():
    """Bullet 4/agentic-meaning check: a regulatory PDF is always ambiguous, so
    Gemini is consulted, and its validated choice actually determines which
    extractor (and therefore which deterministic branch) executes."""
    content = (_DATA_DIR / "filings" / "AZ_HO3_2026_09_synthetic_rate_spec.pdf").read_bytes()
    desc = SourceDescriptor(
        source_id="SRC-PDF-01", name="spec.pdf", source_type=SourceFormat.PDF,
        format="pdf", storage_uri="x",
    )

    # Gemini picks the confident structured extractor.
    fake_confident = FixedExtractorGeminiClient("pdf_structured_section_extractor")
    result_confident = _supervisor(fake_confident).extract_and_compile_source(desc, content)
    assert fake_confident.calls == ["CHOOSE_EXTRACTION_STRATEGY"]
    assert result_confident.evidence["selected_extractor"] == "pdf_structured_section_extractor"
    assert result_confident.evidence["selection_kind"] == "GEMINI"
    assert result_confident.confidence == 0.95
    assert result_confident.requires_human_review is False

    # Gemini instead picks the manual-review extractor for the SAME source —
    # the branch that actually executes changes with the decision.
    fake_cautious = FixedExtractorGeminiClient("pdf_manual_review_extractor")
    result_cautious = _supervisor(fake_cautious).extract_and_compile_source(desc, content)
    assert result_cautious.evidence["selected_extractor"] == "pdf_manual_review_extractor"
    assert result_cautious.evidence["selection_kind"] == "GEMINI"
    assert result_cautious.confidence <= 0.40
    assert result_cautious.requires_human_review is True


def test_ambiguous_excel_triggers_gemini_and_low_confidence_forces_human_review():
    fake = FixedExtractorGeminiClient("excel_manual_review_extractor")
    sup = _supervisor(fake)
    desc = SourceDescriptor(
        source_id="SRC-XLS-02", name="ambiguous.xlsx", source_type=SourceFormat.EXCEL,
        format="xlsx", storage_uri="x",
    )
    result = sup.extract_and_compile_source(desc, _make_ambiguous_excel_bytes())
    assert fake.calls == ["CHOOSE_EXTRACTION_STRATEGY"]
    assert result.evidence["selection_kind"] == "GEMINI"
    assert result.requires_human_review is True
    assert result.confidence < 0.60  # below LOW_CONFIDENCE_REVIEW_THRESHOLD


def test_invented_extractor_id_is_rejected_and_falls_back_to_manual_review():
    """The model may select only an extractor id already registered — an
    invented id must be rejected before anything executes."""
    fake = FixedExtractorGeminiClient("delete_all_prices_extractor")
    sup = _supervisor(fake)
    content = (_DATA_DIR / "filings" / "AZ_HO3_2026_09_synthetic_rate_spec.pdf").read_bytes()
    desc = SourceDescriptor(
        source_id="SRC-PDF-02", name="spec.pdf", source_type=SourceFormat.PDF,
        format="pdf", storage_uri="x",
    )
    result = sup.extract_and_compile_source(desc, content)
    assert result.evidence["selected_extractor"] == "pdf_manual_review_extractor"
    assert result.evidence["selection_kind"] == "FALLBACK"
    assert result.requires_human_review is True


def test_gemini_failure_falls_back_to_conservative_extractor_never_guesses():
    fake = AlwaysFailGeminiClient()
    sup = _supervisor(fake)
    content = (_DATA_DIR / "filings" / "AZ_HO3_2026_09_synthetic_rate_spec.pdf").read_bytes()
    desc = SourceDescriptor(
        source_id="SRC-PDF-03", name="spec.pdf", source_type=SourceFormat.PDF,
        format="pdf", storage_uri="x",
    )
    result = sup.extract_and_compile_source(desc, content)
    assert result.evidence["selected_extractor"] == "pdf_manual_review_extractor"
    assert result.evidence["selection_kind"] == "FALLBACK"
    assert result.requires_human_review is True


def test_extractor_registry_rejects_unknown_ids():
    assert is_known_extractor("pdf_structured_section_extractor")
    assert is_known_extractor("excel_manual_review_extractor")
    assert not is_known_extractor("delete_all_prices_extractor")
    assert not is_known_extractor("")


def test_extraction_evidence_persists_hash_and_selection_metadata():
    fake = AlwaysFailGeminiClient()
    sup = _supervisor(fake)
    content = (_DATA_DIR / "actuarial" / "AZ_HO3_2026_09_rate_spec.json").read_bytes()
    desc = SourceDescriptor(
        source_id="SRC-JSON-EV", name="spec.json", source_type=SourceFormat.STRUCTURED_JSON,
        format="json", storage_uri="x",
    )
    result = sup.extract_and_compile_source(desc, content)

    evidence_records = sup.store.get_evidence("SRC-JSON-EV")
    assert len(evidence_records) == 1
    data = evidence_records[0].data_summary
    assert data["sha256"] == result.evidence["source_sha256"]
    assert len(data["sha256"]) == 64
    assert data["filename"] == "spec.json"
    assert data["selected_extractor"] == "structured_json_direct_parser"


@pytest.mark.parametrize("extractor_id", list(EXTRACTOR_REGISTRY.keys()))
def test_every_registered_extractor_is_actually_reachable(extractor_id):
    """Sanity check that the registry doesn't contain a dangling entry no
    routing path can ever select."""
    spec = EXTRACTOR_REGISTRY[extractor_id]
    assert callable(spec.extract)
    assert 0.0 < spec.confidence_ceiling <= 1.0


def test_ingestion_service_end_to_end_registers_hash_and_compiles_via_supervisor():
    """Confirms the real integration point: PricingSourceIngestionService now
    delegates compilation to AssuranceSupervisor.extract_and_compile_source
    rather than calling an adapter directly, and captures a sha256 hash at
    registration time."""
    from app.services.ingestion_service import PricingSourceIngestionService

    service = PricingSourceIngestionService()
    content = (_DATA_DIR / "actuarial" / "AZ_HO3_2026_09_rate_spec.json").read_bytes()

    descriptor = service.register_source(
        filename="rate_spec.json", content_type="application/json", content=content,
    )
    assert len(descriptor.metadata["sha256"]) == 64

    result = service.compile_source(descriptor)
    assert result.evidence["selected_extractor"] == "structured_json_direct_parser"
    assert result.evidence["selection_kind"] == "DETERMINISTIC"
    assert result.evidence["source_sha256"] == descriptor.metadata["sha256"]


def test_ingestion_service_rejects_excel_and_pdf_uploads():
    """Excel/PDF extraction is not implemented -- the adapters parse (or,
    for PDF, don't even parse) the uploaded bytes and then silently
    substitute the bundled canonical demo IPIR package regardless of actual
    content. Until real, content-faithful extraction exists and is proven,
    the ingestion boundary must fail closed rather than accept these
    formats and misrepresent a fabricated compilation as genuine."""
    from app.adapters.errors import SourceParsingError
    from app.services.ingestion_service import PricingSourceIngestionService

    service = PricingSourceIngestionService()

    for filename, content_type in [
        ("rate_spec.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("filing.pdf", "application/pdf"),
    ]:
        try:
            service.register_source(filename=filename, content_type=content_type, content=b"irrelevant")
            raise AssertionError(f"Expected SourceParsingError for {filename}")
        except SourceParsingError as e:
            assert "not yet supported" in str(e)


def test_ingestion_service_namespaces_package_id_to_source_id():
    """Two different uploads must never collide on the same compiled package
    identity just because the uploaded content declares the same internal
    `id` (e.g. copy-pasted from a shared template)."""
    from app.services.ingestion_service import PricingSourceIngestionService

    service = PricingSourceIngestionService()
    content = (_DATA_DIR / "actuarial" / "AZ_HO3_2026_09_rate_spec.json").read_bytes()

    desc_1 = service.register_source(filename="a.json", content_type="application/json", content=content)
    desc_2 = service.register_source(filename="b.json", content_type="application/json", content=content)

    result_1 = service.compile_source(desc_1)
    result_2 = service.compile_source(desc_2)

    assert result_1.ipir_package.id != result_2.ipir_package.id
    assert desc_1.source_id in result_1.ipir_package.id
    assert desc_2.source_id in result_2.ipir_package.id
