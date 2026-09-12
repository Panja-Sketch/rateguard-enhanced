"""Registry of named extraction strategies AssuranceSupervisor may choose
between for ambiguous sources (a regulatory PDF, or an Excel workbook whose
sheet layout doesn't match the recognized rate-table convention).

This registry does not reimplement extraction — every entry wraps an
already-existing `PricingSourceAdapter`. It exists only to give Gemini's
CHOOSE_EXTRACTION_STRATEGY decision a fixed, named, validated vocabulary to
select from, so an invented extractor id can be rejected before anything
executes.
"""

import io
from collections.abc import Callable
from dataclasses import dataclass

import openpyxl

from app.adapters.excel.adapter import ExcelPricingAdapter
from app.adapters.models import AdapterResult, SourceDescriptor, SourceFormat
from app.adapters.pdf.adapter import PDFPricingAdapter
from app.adapters.platform_config.adapter import PlatformConfigAdapter
from app.adapters.structured_json.adapter import StructuredJSONPricingAdapter

# Sheet-name substrings that indicate a recognized actuarial rate-table layout.
# Purely a routing heuristic — it decides which extractor runs, it never
# influences a pricing value itself.
_RECOGNIZED_EXCEL_SHEET_MARKERS = (
    "factor", "rate", "table", "premium", "metadata", "modifier", "calculation", "fee",
)


@dataclass(frozen=True)
class ExtractorSpec:
    extractor_id: str
    source_format: SourceFormat
    description: str
    confidence_ceiling: float
    extract: Callable[[SourceDescriptor, bytes], AdapterResult]


def _manual_review_extract(
    source: SourceDescriptor, content: bytes, base_adapter: object, note: str
) -> AdapterResult:
    """Honest 'cannot confidently auto-extract' fallback: runs the best-effort
    base adapter but forces low confidence and human review rather than
    silently trusting an unverified extraction of an ambiguous source. Never
    fabricates a pricing value beyond what the base adapter already produces."""
    result = base_adapter.to_ipir(source, content)  # type: ignore[attr-defined]
    result.confidence = min(result.confidence, 0.40)
    result.requires_human_review = True
    result.warnings.append(note)
    return result


_pdf_adapter = PDFPricingAdapter()
_excel_adapter = ExcelPricingAdapter()

EXTRACTOR_REGISTRY: dict[str, ExtractorSpec] = {
    "structured_json_direct_parser": ExtractorSpec(
        extractor_id="structured_json_direct_parser",
        source_format=SourceFormat.STRUCTURED_JSON,
        description="Deterministic parser for already-valid canonical IPIR JSON.",
        confidence_ceiling=1.0,
        extract=StructuredJSONPricingAdapter().to_ipir,
    ),
    "platform_config_adapter": ExtractorSpec(
        extractor_id="platform_config_adapter",
        source_format=SourceFormat.PLATFORM_CONFIG,
        description="Deterministic parser for recognized rating-platform configuration JSON.",
        confidence_ceiling=1.0,
        extract=PlatformConfigAdapter().to_ipir,
    ),
    "excel_named_range_extractor": ExtractorSpec(
        extractor_id="excel_named_range_extractor",
        source_format=SourceFormat.EXCEL,
        description=(
            "Deterministic extraction for actuarial workbooks with a recognized "
            "rate-table sheet layout."
        ),
        confidence_ceiling=1.0,
        extract=_excel_adapter.to_ipir,
    ),
    "excel_manual_review_extractor": ExtractorSpec(
        extractor_id="excel_manual_review_extractor",
        source_format=SourceFormat.EXCEL,
        description=(
            "Fallback for an Excel workbook whose sheet layout does not match the "
            "recognized rate-table convention; always requires human review."
        ),
        confidence_ceiling=0.40,
        extract=lambda source, content: _manual_review_extract(
            source, content, _excel_adapter,
            "Workbook sheet layout did not match the recognized rate-table convention; "
            "extraction confidence is capped and human verification is required before use.",
        ),
    ),
    "pdf_structured_section_extractor": ExtractorSpec(
        extractor_id="pdf_structured_section_extractor",
        source_format=SourceFormat.PDF,
        description=(
            "Deterministic section/page-based text extraction for well-formed "
            "regulatory PDF filings."
        ),
        confidence_ceiling=0.95,
        extract=_pdf_adapter.to_ipir,
    ),
    "pdf_manual_review_extractor": ExtractorSpec(
        extractor_id="pdf_manual_review_extractor",
        source_format=SourceFormat.PDF,
        description=(
            "Fallback for a PDF whose structure could not be confidently parsed; "
            "always requires human review."
        ),
        confidence_ceiling=0.40,
        extract=lambda source, content: _manual_review_extract(
            source, content, _pdf_adapter,
            "PDF structure could not be confidently parsed; extraction confidence is "
            "capped and human verification is required before use.",
        ),
    ),
}


def is_known_extractor(extractor_id: str) -> bool:
    return extractor_id in EXTRACTOR_REGISTRY


def extractors_for_format(source_format: SourceFormat) -> list[str]:
    return [eid for eid, spec in EXTRACTOR_REGISTRY.items() if spec.source_format == source_format]


def excel_layout_recognized(content: bytes) -> bool:
    """Real (if simple) structural check: does this workbook contain at least
    one sheet name matching the recognized actuarial rate-table convention?
    Used only to decide *which extractor runs* — never to derive a value."""
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    except Exception:
        return False
    sheet_names = [s.lower() for s in wb.sheetnames]
    return any(marker in name for name in sheet_names for marker in _RECOGNIZED_EXCEL_SHEET_MARKERS)
