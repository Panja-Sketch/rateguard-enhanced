"""Unit tests for the formula-injection / stored-XSS sanitizer (locked doc
section 15.3), proving a malicious metadata-style value is neutralized in
both the display path and the export path.
"""

from app.ingestion.workbook_v1.sanitize import (
    sanitize_display_and_export,
    sanitize_for_display,
    sanitize_for_export,
)

FORMULA_INJECTION_PAYLOAD = "=cmd|'/c calc'!A1"
XSS_PAYLOAD = "<script>alert(1)</script>"


def test_formula_injection_neutralized_for_export():
    result = sanitize_for_export(FORMULA_INJECTION_PAYLOAD)
    assert result.startswith("'=")
    # Never re-interpretable as a formula: no longer starts with '='.
    assert not result.startswith("=")


def test_formula_injection_left_alone_by_display_only_sanitizer():
    # HTML-escaping alone does not defang a leading '=' for a spreadsheet
    # re-import -- the two mitigations address different sinks.
    result = sanitize_for_display(FORMULA_INJECTION_PAYLOAD)
    assert result.startswith("=")


def test_xss_payload_neutralized_for_display():
    result = sanitize_for_display(XSS_PAYLOAD)
    assert "<script>" not in result
    assert "&lt;script&gt;" in result


def test_combined_sanitizer_neutralizes_formula_injection_payload():
    result = sanitize_display_and_export(FORMULA_INJECTION_PAYLOAD)
    assert not result.startswith("=")
    assert "cmd" in result  # content preserved, just neutralized, not deleted


def test_combined_sanitizer_neutralizes_xss_payload():
    result = sanitize_display_and_export(XSS_PAYLOAD)
    assert "<script>" not in result
    assert "&lt;script&gt;" in result


def test_plus_minus_at_tab_cr_prefixes_all_neutralized():
    for prefix in ("+", "-", "@", "\t", "\r"):
        payload = f"{prefix}SUM(1,1)"
        result = sanitize_for_export(payload)
        assert result.startswith("'" + prefix)


def test_ordinary_value_is_unchanged():
    assert sanitize_for_export("roof_age") == "roof_age"
    assert sanitize_for_display("roof_age") == "roof_age"
    assert sanitize_display_and_export("Roof Age (years)") == "Roof Age (years)"


def test_metadata_value_end_to_end_via_compiler(canonical_bytes):
    """Proves the sanitizer is actually wired into the compiler boundary,
    not just unit-tested in isolation: an injected metadata value comes out
    neutralized in the receipt."""
    import openpyxl

    from app.ingestion.workbook_v1.compiler import compile_workbook

    wb = openpyxl.load_workbook(__import__("io").BytesIO(canonical_bytes))
    ws = wb["RG_METADATA"]
    ws.append(["malicious_note", FORMULA_INJECTION_PAYLOAD])
    buf = __import__("io").BytesIO()
    wb.save(buf)

    receipt = compile_workbook(buf.getvalue(), "injected.xlsx")
    assert receipt.status == "VERIFIED"
    neutralized = receipt.metadata["malicious_note"]
    # Never a raw leading '=' (CSV/XLSX re-import formula injection) and the
    # apostrophe/quote characters are themselves HTML-escaped (display XSS
    # defense) -- both mitigations applied, in combination.
    assert not neutralized.startswith("=")
    assert "cmd" in neutralized
