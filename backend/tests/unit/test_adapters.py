from pathlib import Path

from app.adapters import (
    ExcelPricingAdapter,
    PDFPricingAdapter,
    PlatformConfigAdapter,
    SourceDescriptor,
    SourceFormat,
    StructuredJSONPricingAdapter,
    get_adapter_registry,
)


def test_adapter_registry():
    """Verifies that default adapters are registered in AdapterRegistry."""
    registry = get_adapter_registry()
    assert registry.get_adapter(SourceFormat.STRUCTURED_JSON) is not None
    assert registry.get_adapter(SourceFormat.EXCEL) is not None
    assert registry.get_adapter(SourceFormat.PDF) is not None
    assert registry.get_adapter(SourceFormat.PLATFORM_CONFIG) is not None


def test_structured_json_adapter():
    """Tests compiling structured JSON rate spec to IPIRPackage."""
    root_dir = Path(__file__).resolve().parent.parent.parent.parent
    json_file = root_dir / "data" / "actuarial" / "AZ_HO3_2026_09_rate_spec.json"

    with open(json_file, "rb") as f:
        content = f.read()

    adapter = StructuredJSONPricingAdapter()
    desc = SourceDescriptor(
        source_id="TEST-SRC-JSON",
        name="test_spec.json",
        source_type=SourceFormat.STRUCTURED_JSON,
        format="json",
        storage_uri=str(json_file),
    )

    res = adapter.to_ipir(desc, content)
    assert res.ipir_package.id == "AZ_HO3_2026_09"
    assert res.mapping_coverage == 100.0
    assert res.confidence == 1.0


def test_excel_pricing_adapter():
    """Tests compiling synthetic Excel rate spec to IPIRPackage."""
    root_dir = Path(__file__).resolve().parent.parent.parent.parent
    excel_file = root_dir / "data" / "actuarial" / "AZ_HO3_2026_09_rate_spec.xlsx"

    with open(excel_file, "rb") as f:
        content = f.read()

    adapter = ExcelPricingAdapter()
    desc = SourceDescriptor(
        source_id="TEST-SRC-EXCEL",
        name="test_spec.xlsx",
        source_type=SourceFormat.EXCEL,
        format="xlsx",
        storage_uri=str(excel_file),
    )

    res = adapter.to_ipir(desc, content)
    assert res.ipir_package.id == "AZ_HO3_2026_09"
    assert res.mapping_coverage == 100.0


def test_pdf_pricing_adapter():
    """Tests compiling synthetic PDF rate spec to IPIRPackage."""
    root_dir = Path(__file__).resolve().parent.parent.parent.parent
    pdf_file = root_dir / "data" / "filings" / "AZ_HO3_2026_09_synthetic_rate_spec.pdf"

    with open(pdf_file, "rb") as f:
        content = f.read()

    adapter = PDFPricingAdapter()
    desc = SourceDescriptor(
        source_id="TEST-SRC-PDF",
        name="test_spec.pdf",
        source_type=SourceFormat.PDF,
        format="pdf",
        storage_uri=str(pdf_file),
    )

    res = adapter.to_ipir(desc, content)
    assert res.ipir_package.id == "AZ_HO3_2026_09"
    assert res.mapping_coverage == 95.0
    assert res.confidence == 0.95


def test_platform_config_adapter():
    """Tests compiling platform config JSON to IPIRPackage."""
    root_dir = Path(__file__).resolve().parent.parent.parent.parent
    plat_file = (
        root_dir
        / "data"
        / "implementations"
        / "platform_config"
        / "AZ_HO3_2026_09_platform_config.json"
    )

    with open(plat_file, "rb") as f:
        content = f.read()

    adapter = PlatformConfigAdapter()
    desc = SourceDescriptor(
        source_id="TEST-SRC-PLATFORM",
        name="test_config.json",
        source_type=SourceFormat.PLATFORM_CONFIG,
        format="json",
        storage_uri=str(plat_file),
    )

    res = adapter.to_ipir(desc, content)
    assert res.ipir_package.id == "AZ_HO3_2026_09"
    assert res.mapping_coverage == 100.0
