"""Golden workbook tests (locked doc section 17.1) and the full negative
fixture suite (session instructions item 12) for the Controlled Workbook v1
compiler. Every test calls the real `compile_workbook` entrypoint against
real generated `.xlsx` bytes -- no mocking of the parser or the security
checks.
"""

from app.ingestion.workbook_v1.compiler import compile_workbook

from .conftest import negative_sample


# ---------------------------------------------------------------------------
# Positive / golden cases (locked doc section 17.1 bullets 1-2)
# ---------------------------------------------------------------------------


def test_canonical_workbook_compiles_and_produces_700(canonical_bytes):
    receipt = compile_workbook(canonical_bytes, "canonical.xlsx")
    assert receipt.status == "VERIFIED"
    assert receipt.errors == []
    assert len(receipt.control_case_results) == 1
    result = receipt.control_case_results[0]
    assert result.passed is True
    assert result.actual == "700.00"
    assert result.expected == "700.00"


def test_defective_workbook_compiles_and_control_case_proves_655(defective_bytes):
    # Scoping note (session instructions, A2-scoping judgment call): A2 in
    # the locked acceptance table is about comparing a canonical *spec*
    # against a defective *target engine* via a mission -- that comparison
    # is out of scope for a workbook *compiler* unit test. This test only
    # asserts the narrower, literal workbook-compiler concern: a workbook
    # that correctly and consistently declares the defective pricing rule
    # (roof_age_factor=1.31) is itself an internally self-consistent,
    # compilable IPIR source whose own embedded control case proves its own
    # declared premium ($655.00). It is not a claim that $655 is "the right"
    # premium, nor does it perform any canonical-vs-defective cross-engine
    # comparison -- that belongs to the mission-level reconciliation engine
    # (locked doc section 7), not this compiler.
    receipt = compile_workbook(defective_bytes, "defective.xlsx")
    assert receipt.status == "VERIFIED"
    assert receipt.errors == []
    result = receipt.control_case_results[0]
    assert result.passed is True
    assert result.actual == "655.00"


# ---------------------------------------------------------------------------
# Locked doc section 17.1 golden negative bullets
# ---------------------------------------------------------------------------


def test_unknown_function_rejects_matching_a8():
    receipt = compile_workbook(negative_sample("unsupported_function.xlsx"), "x.xlsx")
    assert receipt.status == "REJECTED"
    assert receipt.errors[0].code == "UNSUPPORTED_WORKBOOK_FUNCTION"
    detail = receipt.errors[0].details[0]
    assert detail["sheet"] == "RG_CALCULATIONS"
    assert detail["function"] == "INDIRECT"


def test_macro_enabled_workbook_rejects():
    receipt = compile_workbook(negative_sample("macro_enabled.xlsx"), "x.xlsx")
    assert receipt.status == "REJECTED"
    assert receipt.errors[0].code == "VBA_PROJECT_PRESENT"


def test_macro_enabled_via_xlsm_extension_rejects():
    receipt = compile_workbook(negative_sample("wrong_extension.xlsm"), "x.xlsm")
    assert receipt.status == "REJECTED"
    assert receipt.errors[0].code == "INVALID_FILE_EXTENSION"


def test_external_link_rejects():
    receipt = compile_workbook(negative_sample("external_link.xlsx"), "x.xlsx")
    assert receipt.status == "REJECTED"
    assert receipt.errors[0].code == "EXTERNAL_LINK_PRESENT"


def test_ole_embedded_object_rejects():
    receipt = compile_workbook(negative_sample("ole_embedded_object.xlsx"), "x.xlsx")
    assert receipt.status == "REJECTED"
    assert receipt.errors[0].code == "OLE_EMBEDDED_OBJECT"


def test_password_protected_encrypted_workbook_rejects():
    receipt = compile_workbook(negative_sample("encrypted_workbook.xlsx"), "x.xlsx")
    assert receipt.status == "REJECTED"
    assert receipt.errors[0].code == "ENCRYPTED_WORKBOOK"


def test_hidden_dependency_outside_contract_rejects():
    receipt = compile_workbook(negative_sample("hidden_dependency.xlsx"), "x.xlsx")
    assert receipt.status == "REJECTED"
    assert receipt.errors[0].code == "UNRESOLVED_REFERENCE"


def test_duplicate_ids_reject():
    receipt = compile_workbook(negative_sample("duplicate_ids.xlsx"), "x.xlsx")
    assert receipt.status == "REJECTED"
    assert receipt.errors[0].code == "DUPLICATE_ID"


def test_missing_control_case_causes_review_required():
    receipt = compile_workbook(negative_sample("missing_control_case.xlsx"), "x.xlsx")
    assert receipt.status == "REVIEW_REQUIRED"
    assert receipt.errors == []
    assert any("control case" in w.lower() for w in receipt.warnings)


def test_overlapping_ambiguous_ranges_reject():
    receipt = compile_workbook(negative_sample("overlapping_ranges.xlsx"), "x.xlsx")
    assert receipt.status == "REJECTED"
    assert receipt.errors[0].code == "AMBIGUOUS_RANGE_OVERLAP"


def test_formula_cycle_rejects():
    receipt = compile_workbook(negative_sample("formula_cycle.xlsx"), "x.xlsx")
    assert receipt.status == "REJECTED"
    assert receipt.errors[0].code == "CALCULATION_CYCLE"


def test_tampered_workbook_changes_source_hash_and_invalidates_attestation(canonical_bytes):
    original_receipt = compile_workbook(canonical_bytes, "canonical.xlsx")
    assert original_receipt.status == "VERIFIED"

    tampered = bytearray(canonical_bytes)
    # Flip one byte deep in the archive (past the local-file-header magic,
    # inside compressed part data) so the ZIP still opens but the content
    # hash changes.
    flip_index = len(tampered) - 200
    tampered[flip_index] ^= 0xFF
    tampered_receipt = compile_workbook(bytes(tampered), "canonical.xlsx")

    assert tampered_receipt.artifact_sha256 != original_receipt.artifact_sha256
    # A prior VERIFIED attestation is keyed to the original hash; the new
    # receipt (whatever its status) never reuses or reports that old hash,
    # so any consumer keying trust off `artifact_sha256` cannot mistake the
    # tampered bytes for the previously-attested-verified artifact.
    if tampered_receipt.package is not None:
        assert tampered_receipt.package.source.artifact_sha256 == tampered_receipt.artifact_sha256
        assert tampered_receipt.package.source.artifact_sha256 != original_receipt.artifact_sha256


# ---------------------------------------------------------------------------
# Additional required negative coverage (session instructions item 12)
# ---------------------------------------------------------------------------


def test_oversized_file_rejects():
    receipt = compile_workbook(negative_sample("oversized_file.xlsx"), "x.xlsx")
    assert receipt.status == "REJECTED"
    assert receipt.errors[0].code == "FILE_TOO_LARGE"


def test_zip_bomb_compression_ratio_rejects():
    receipt = compile_workbook(negative_sample("zip_bomb.xlsx"), "x.xlsx")
    assert receipt.status == "REJECTED"
    assert receipt.errors[0].code == "ZIP_BOMB_SUSPECTED"


def test_excessive_zip_entry_count_rejects():
    receipt = compile_workbook(negative_sample("excessive_entries.xlsx"), "x.xlsx")
    assert receipt.status == "REJECTED"
    assert receipt.errors[0].code == "EXCESSIVE_ZIP_ENTRIES"


def test_path_traversal_entry_name_rejects():
    receipt = compile_workbook(negative_sample("path_traversal.xlsx"), "x.xlsx")
    assert receipt.status == "REJECTED"
    assert receipt.errors[0].code == "PATH_TRAVERSAL_ENTRY"


def test_non_xlsx_extension_rejects():
    receipt = compile_workbook(negative_sample("wrong_extension.xlsm"), "x.xlsm")
    assert receipt.status == "REJECTED"
    assert receipt.errors[0].code == "INVALID_FILE_EXTENSION"


def test_bad_file_signature_rejects():
    receipt = compile_workbook(negative_sample("bad_signature.xlsx"), "x.xlsx")
    assert receipt.status == "REJECTED"
    assert receipt.errors[0].code == "INVALID_FILE_SIGNATURE"


def test_missing_required_sheet_rejects():
    receipt = compile_workbook(negative_sample("missing_required_sheet.xlsx"), "x.xlsx")
    assert receipt.status == "REJECTED"
    assert receipt.errors[0].code == "MISSING_REQUIRED_SHEET"
    assert receipt.errors[0].details[0]["sheet"] == "RG_TABLES"


def test_missing_required_column_rejects():
    receipt = compile_workbook(negative_sample("missing_required_column.xlsx"), "x.xlsx")
    assert receipt.status == "REJECTED"
    assert receipt.errors[0].code == "MISSING_REQUIRED_COLUMN"


def test_division_by_zero_path_rejects():
    receipt = compile_workbook(negative_sample("division_by_zero.xlsx"), "x.xlsx")
    assert receipt.status == "REJECTED"
    assert receipt.errors[0].code == "DIVISION_BY_ZERO_PATH"


def test_missing_rounding_on_output_rejects():
    receipt = compile_workbook(negative_sample("missing_rounding.xlsx"), "x.xlsx")
    assert receipt.status == "REJECTED"
    assert receipt.errors[0].code == "MISSING_ROUNDING"


def test_currency_inconsistency_across_outputs_rejects():
    receipt = compile_workbook(negative_sample("currency_inconsistency.xlsx"), "x.xlsx")
    assert receipt.status == "REJECTED"
    assert receipt.errors[0].code == "CURRENCY_INCONSISTENCY"


def test_empty_file_rejects():
    receipt = compile_workbook(b"", "empty.xlsx")
    assert receipt.status == "REJECTED"
    assert receipt.errors[0].code == "CORRUPT_ARCHIVE"


def test_never_raises_always_returns_a_receipt(canonical_bytes):
    """`compile_workbook` is documented to never raise to its caller --
    every failure mode must surface as a `REJECTED`/`REVIEW_REQUIRED`
    receipt instead."""
    for payload, name in [
        (b"garbage", "a.xlsx"),
        (canonical_bytes, "a.txt"),
        (b"", "a.xlsx"),
    ]:
        receipt = compile_workbook(payload, name)
        assert receipt.status in ("VERIFIED", "REVIEW_REQUIRED", "REJECTED")
