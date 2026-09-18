#!/usr/bin/env python
"""Deterministically generates real `.xlsx` sample workbooks for the
Controlled Workbook v1 compiler (`backend/app/ingestion/workbook_v1`),
mirroring the style of `generate_ipir_v0_2_golden_fixtures.py`.

All positive workbooks are built purely with `openpyxl` cell writes (plain
values, never Excel formulas the compiler would need to execute). Negative
fixtures that require constructs openpyxl cannot itself express (a VBA
project entry, a corrupted ZIP entry name, an injected raw Excel formula
string, a genuinely encrypted OLE container) are produced by direct
`zipfile`/byte manipulation of an already-valid base workbook, each
documented inline with why. Nothing here is hand-edited binary; re-running
this script reproduces byte-identical output (deterministic dates/hashes,
no wall-clock/randomness).

Usage: python backend/scripts/generate_workbook_v1_samples.py
"""

from __future__ import annotations

import hashlib
import io
import shutil
import sys
import zipfile
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SAMPLES_ROOT = REPO_ROOT / "data" / "samples" / "workbook_v1"


def _write_sheet(wb, name: str, header: list[str], rows: list[list]) -> None:
    ws = wb.create_sheet(name)
    ws.append(header)
    for row in rows:
        ws.append(row)


def build_golden_workbook(*, roof_age_21_plus_factor: str, expected_premium: str) -> openpyxl.Workbook:
    """Builds a workbook reproducing the same locked golden narrative as the
    IPIR v0.2 fixtures: base_rate=500.00 x roof_age_factor, roof_age=25."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # drop the default blank sheet

    _write_sheet(
        wb, "RG_METADATA", ["key", "value"],
        [
            ["package_id", "az_ho3_workbook_golden"],
            ["package_version", "1.0.0"],
            ["product_id", "az_ho3"],
            ["line", "HOMEOWNERS"],
            ["country", "US"],
            ["state", "AZ"],
            ["currency", "USD"],
            ["effective_start", "2026-10-01"],
            ["effective_end", ""],
            ["transaction_types", "NEW_BUSINESS,RENEWAL"],
        ],
    )
    _write_sheet(
        wb, "RG_INPUTS",
        ["id", "name", "data_type", "required", "minimum", "maximum", "allowed_values"],
        [
            ["roof_age", "Roof Age (years)", "INTEGER", "TRUE", 0, "", ""],
            ["dwelling_limit", "Dwelling Coverage Limit", "MONEY", "TRUE", "", "", ""],
        ],
    )
    _write_sheet(
        wb, "RG_CONSTANTS", ["id", "name", "value"],
        [["base_rate", "Base Rate", "500.00"]],
    )
    _write_sheet(
        wb, "RG_TABLES",
        ["table_id", "dimension_id", "min", "max", "include_min", "include_max", "match_value", "priority", "result_value"],
        [
            ["roof_age_factor_table", "roof_age", 0, 10, "TRUE", "TRUE", "", "", "1.00"],
            ["roof_age_factor_table", "roof_age", 11, 20, "TRUE", "TRUE", "", "", "1.20"],
            ["roof_age_factor_table", "roof_age", 21, "", "TRUE", "TRUE", "", "", roof_age_21_plus_factor],
        ],
    )
    # NOTE: RG_TABLES column order above intentionally differs from the
    # locked doc's listed order (result_value last, priority before it) --
    # the compiler resolves columns by header name, not position, so this
    # also exercises that the compiler does not assume a fixed column order.
    _write_sheet(
        wb, "RG_CALCULATIONS",
        ["node_id", "operator", "operand_1", "operand_2", "rounding_mode", "scale"],
        [
            ["rate_factor", "LOOKUP", "roof_age_factor_table", "", "", ""],
            ["raw_premium", "MULTIPLY", "base_rate", "rate_factor", "", ""],
            ["final_premium", "ROUND", "raw_premium", "", "HALF_UP", 2],
        ],
    )
    _write_sheet(
        wb, "RG_OUTPUTS", ["output_id", "source_ref", "currency"],
        [["final_premium_output", "final_premium", "USD"]],
    )
    _write_sheet(
        wb, "RG_CONTROL_CASES", ["case_id", "input", "expected_output", "tolerance"],
        [
            [
                "golden_case",
                '{"roof_age": 25, "dwelling_limit": "300000.00"}',
                f'{{"final_premium_output": "{expected_premium}"}}',
                "0.00",
            ]
        ],
    )
    return wb


def _save(wb: openpyxl.Workbook, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    print(f"Wrote {path.relative_to(REPO_ROOT)}")


def _load_bytes(path: Path) -> bytes:
    return path.read_bytes()


def _save_bytes(content: bytes, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    print(f"Wrote {path.relative_to(REPO_ROOT)}")


def _rewrite_zip_with(
    base_content: bytes,
    *,
    add_entries: dict[str, bytes] | None = None,
    replace_entries: dict[str, bytes] | None = None,
    rename_entries: dict[str, str] | None = None,
    remove_entries: set[str] | None = None,
) -> bytes:
    """Rebuilds a ZIP archive from `base_content`, applying entry-level
    edits. This is how every negative fixture that isn't expressible via
    plain openpyxl cell writes is produced -- programmatic ZIP surgery on a
    known-good base, never a hand-crafted binary blob."""
    add_entries = add_entries or {}
    replace_entries = replace_entries or {}
    rename_entries = rename_entries or {}
    remove_entries = remove_entries or set()

    src = zipfile.ZipFile(io.BytesIO(base_content))
    out_buf = io.BytesIO()
    with zipfile.ZipFile(out_buf, "w", zipfile.ZIP_DEFLATED) as out:
        for item in src.infolist():
            if item.filename in remove_entries:
                continue
            data = src.read(item.filename)
            if item.filename in replace_entries:
                data = replace_entries[item.filename]
            out_name = rename_entries.get(item.filename, item.filename)
            out.writestr(out_name, data)
        for name, data in add_entries.items():
            out.writestr(name, data)
    return out_buf.getvalue()


def generate_positive_samples() -> dict[str, bytes]:
    canonical_wb = build_golden_workbook(roof_age_21_plus_factor="1.40", expected_premium="700.00")
    defective_wb = build_golden_workbook(roof_age_21_plus_factor="1.31", expected_premium="655.00")

    canonical_path = SAMPLES_ROOT / "canonical" / "AZ_HO3_GOLDEN_workbook.xlsx"
    defective_path = SAMPLES_ROOT / "defective" / "AZ_HO3_GOLDEN_workbook.xlsx"
    _save(canonical_wb, canonical_path)
    _save(defective_wb, defective_path)
    return {
        "canonical": _load_bytes(canonical_path),
        "defective": _load_bytes(defective_path),
    }


def generate_negative_samples(canonical_bytes: bytes) -> None:
    neg_dir = SAMPLES_ROOT / "negative"

    # 1. Unsupported function: replace a calculation operand with a raw
    # Excel formula string using a function outside the allowlist. Real
    # openpyxl cell write; the compiler's defensive raw-formula scan (not
    # execution) is what must catch this (locked doc A8).
    wb = build_golden_workbook(roof_age_21_plus_factor="1.40", expected_premium="700.00")
    wb["RG_CALCULATIONS"]["C2"] = "=INDIRECT(\"A1\")"
    _save(wb, neg_dir / "unsupported_function.xlsx")

    # 2. Duplicate IDs: two RG_CONSTANTS rows share the same id.
    wb = build_golden_workbook(roof_age_21_plus_factor="1.40", expected_premium="700.00")
    ws = wb["RG_CONSTANTS"]
    ws.append(["base_rate", "Duplicate Base Rate", "999.00"])
    _save(wb, neg_dir / "duplicate_ids.xlsx")

    # 3. Overlapping ambiguous ranges: two RG_TABLES rows overlap with no
    # distinct priority.
    wb = build_golden_workbook(roof_age_21_plus_factor="1.40", expected_premium="700.00")
    ws = wb["RG_TABLES"]
    ws.append(["roof_age_factor_table", "roof_age", 15, 25, "TRUE", "TRUE", "", "", "9.99"])
    _save(wb, neg_dir / "overlapping_ranges.xlsx")

    # 4. Formula cycle: final_premium depends on raw_premium which depends
    # (via a rewritten operand) back on final_premium.
    wb = build_golden_workbook(roof_age_21_plus_factor="1.40", expected_premium="700.00")
    ws = wb["RG_CALCULATIONS"]
    for row in ws.iter_rows(min_row=2):
        if row[0].value == "raw_premium":
            row[3].value = "final_premium"  # operand_2: raw_premium = base_rate * final_premium
    _save(wb, neg_dir / "formula_cycle.xlsx")

    # 5. Hidden dependency outside contract: an output references a node id
    # that is never declared anywhere in RG_CALCULATIONS.
    wb = build_golden_workbook(roof_age_21_plus_factor="1.40", expected_premium="700.00")
    ws = wb["RG_OUTPUTS"]
    ws["B2"] = "undeclared_node"
    _save(wb, neg_dir / "hidden_dependency.xlsx")

    # 6. Missing control case: RG_CONTROL_CASES sheet present but empty
    # (header only) -> REVIEW_REQUIRED, not REJECTED.
    wb = build_golden_workbook(roof_age_21_plus_factor="1.40", expected_premium="700.00")
    ws = wb["RG_CONTROL_CASES"]
    for row in list(ws.iter_rows(min_row=2)):
        ws.delete_rows(row[0].row, 1)
    _save(wb, neg_dir / "missing_control_case.xlsx")

    # 7. Missing required sheet: drop RG_TABLES entirely.
    wb = build_golden_workbook(roof_age_21_plus_factor="1.40", expected_premium="700.00")
    del wb["RG_TABLES"]
    _save(wb, neg_dir / "missing_required_sheet.xlsx")

    # 8. Missing required column: rename a required header on RG_INPUTS.
    wb = build_golden_workbook(roof_age_21_plus_factor="1.40", expected_premium="700.00")
    wb["RG_INPUTS"]["D1"] = "is_required"  # was "required"
    _save(wb, neg_dir / "missing_required_column.xlsx")

    # 9. Division-by-zero path: a DIVIDE node whose second operand is a
    # literal zero.
    wb = build_golden_workbook(roof_age_21_plus_factor="1.40", expected_premium="700.00")
    ws = wb["RG_CALCULATIONS"]
    ws.append(["broken_ratio", "DIVIDE", "base_rate", "#0", "", ""])
    _save(wb, neg_dir / "division_by_zero.xlsx")

    # 10. Missing rounding: RG_OUTPUTS references a calculation node whose
    # top-level operator is not ROUND.
    wb = build_golden_workbook(roof_age_21_plus_factor="1.40", expected_premium="700.00")
    wb["RG_OUTPUTS"]["B2"] = "raw_premium"  # raw_premium's operator is MULTIPLY, not ROUND
    _save(wb, neg_dir / "missing_rounding.xlsx")

    # 11. Currency inconsistency: a second output row with a different
    # currency than the first.
    wb = build_golden_workbook(roof_age_21_plus_factor="1.40", expected_premium="700.00")
    ws = wb["RG_CALCULATIONS"]
    ws.append(["final_premium_eur", "ROUND", "raw_premium", "", "HALF_UP", 2])
    ws2 = wb["RG_OUTPUTS"]
    ws2.append(["final_premium_output_eur", "final_premium_eur", "EUR"])
    _save(wb, neg_dir / "currency_inconsistency.xlsx")

    # 12. Non-.xlsx extension / wrong signature: same bytes, wrong name (the
    # compiler must reject on extension before ever touching ZIP structure).
    # Also emit a byte-signature mismatch fixture: valid-looking name, but
    # content is plain text, not a ZIP at all.
    _save_bytes(canonical_bytes, neg_dir / "wrong_extension.xlsm")
    _save_bytes(b"this is not a zip file at all, just text pretending to be xlsx", neg_dir / "bad_signature.xlsx")

    # 13. Path traversal entry name: rename a real ZIP entry to escape the
    # archive root.
    traversal_bytes = _rewrite_zip_with(
        canonical_bytes, rename_entries={"xl/worksheets/sheet1.xml": "../../etc/passwd"}
    )
    _save_bytes(traversal_bytes, neg_dir / "path_traversal.xlsx")

    # 14. VBA project present: add a fake xl/vbaProject.bin entry (real VBA
    # binary content is irrelevant -- the compiler rejects on the entry's
    # mere presence, never attempts to parse or execute it) and declare the
    # macro-enabled content type, matching what a real .xlsm produces.
    content_types = zipfile.ZipFile(io.BytesIO(canonical_bytes)).read("[Content_Types].xml").decode("utf-8")
    macro_content_types = content_types.replace(
        "</Types>",
        '<Override PartName="/xl/vbaProject.bin" '
        'ContentType="application/vnd.ms-office.vbaProject"/></Types>',
    )
    macro_bytes = _rewrite_zip_with(
        canonical_bytes,
        add_entries={"xl/vbaProject.bin": b"\x00\x01FAKE-VBA-PROJECT-BINARY-NOT-EXECUTED\x00"},
        replace_entries={"[Content_Types].xml": macro_content_types.encode("utf-8")},
    )
    _save_bytes(macro_bytes, neg_dir / "macro_enabled.xlsx")

    # 15. External link: add an external-links part plus its relationship,
    # matching the real OOXML shape Excel produces for "Edit Links".
    external_link_xml = (
        b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        b'<externalLink xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        b'<externalBook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        b'r:id="rId1"/></externalLink>'
    )
    external_link_bytes = _rewrite_zip_with(
        canonical_bytes,
        add_entries={
            "xl/externalLinks/externalLink1.xml": external_link_xml,
            "xl/externalLinks/_rels/externalLink1.xml.rels": (
                b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                b'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                b'relationships/externalLinkPath/externalBook" Target="http://evil.example.com/book.xlsx" '
                b'TargetMode="External"/></Relationships>'
            ),
        },
    )
    _save_bytes(external_link_bytes, neg_dir / "external_link.xlsx")

    # 16. OLE/embedded object: add an embeddings part.
    ole_bytes = _rewrite_zip_with(
        canonical_bytes,
        add_entries={"xl/embeddings/oleObject1.bin": b"\xd0\xcf\x11\xe0FAKE-OLE-OBJECT-NOT-OPENED"},
    )
    _save_bytes(ole_bytes, neg_dir / "ole_embedded_object.xlsx")

    # 17. Encrypted / password-protected: a genuine encrypted .xlsx is
    # stored as an OLE Compound File Binary container, not a ZIP at all (the
    # real ZIP payload is wrapped inside an ECMA-376/MS-OFFCRYPTO encryption
    # stream). openpyxl has no API to *produce* an encrypted file (writing
    # one requires the proprietary CFB/RC4-or-AES encryption wrapper, which
    # is exactly the "no execution of unsafe formats" boundary this project
    # deliberately never implements). This fixture is therefore a minimal,
    # honestly-labeled OLE/CFB stub: the real 8-byte CFB signature Microsoft
    # Office and this compiler both recognize, followed by deterministic
    # zero-padding out to a plausible minimum CFB header size (no attempt to
    # fabricate real OLE sector/FAT structure, since the compiler's check
    # only needs to inspect the leading signature -- see
    # docs/implementation/STATUS.md and DECISIONS.md D5 for why this is a
    # documented limitation of the fixture, not a simulated flag).
    from app.ingestion.workbook_v1.limits import OLE_CFB_SIGNATURE

    encrypted_stub = OLE_CFB_SIGNATURE + (b"\x00" * 512)
    _save_bytes(encrypted_stub, neg_dir / "encrypted_workbook.xlsx")

    # 18. Excessive ZIP-entry count: pad the archive with many trivial extra
    # entries past MAX_ZIP_ENTRIES.
    from app.ingestion.workbook_v1.limits import MAX_ZIP_ENTRIES

    padding_entries = {f"pad/entry_{i:05d}.txt": b"x" for i in range(MAX_ZIP_ENTRIES + 10)}
    excessive_entries_bytes = _rewrite_zip_with(canonical_bytes, add_entries=padding_entries)
    _save_bytes(excessive_entries_bytes, neg_dir / "excessive_entries.xlsx")

    # 19. ZIP bomb / excessive compression ratio: one entry whose
    # uncompressed size vastly exceeds its compressed size (highly
    # repetitive content compresses extremely well, which is exactly what a
    # real zip-bomb payload exploits).
    from app.ingestion.workbook_v1.limits import MIN_ENTRY_SIZE_FOR_RATIO_CHECK

    bomb_payload = b"0" * (MIN_ENTRY_SIZE_FOR_RATIO_CHECK * 500)
    zip_bomb_bytes = _rewrite_zip_with(canonical_bytes, add_entries={"pad/bomb.txt": bomb_payload})
    _save_bytes(zip_bomb_bytes, neg_dir / "zip_bomb.xlsx")

    # 20. Oversized file: pad well past the 10 MiB upload limit with
    # deterministic, high-entropy (SHA-256-stream) bytes -- not a repeating
    # pattern, so DEFLATE cannot compress it away and the *archive file
    # itself* (not just one entry's compression ratio) ends up over the
    # limit, exercising FILE_TOO_LARGE specifically rather than
    # ZIP_BOMB_SUSPECTED.
    from app.ingestion.workbook_v1.limits import MAX_UPLOAD_BYTES

    def _high_entropy_bytes(n: int) -> bytes:
        out = bytearray()
        counter = 0
        seed = b"rateguard-workbook-v1-oversized-fixture"
        while len(out) < n:
            out.extend(hashlib.sha256(seed + counter.to_bytes(4, "big")).digest())
            counter += 1
        return bytes(out[:n])

    oversized_bytes = _rewrite_zip_with(
        canonical_bytes, add_entries={"pad/oversized.bin": _high_entropy_bytes(MAX_UPLOAD_BYTES + 1024)}
    )
    _save_bytes(oversized_bytes, neg_dir / "oversized_file.xlsx")


def main() -> None:
    if SAMPLES_ROOT.exists():
        shutil.rmtree(SAMPLES_ROOT)
    positives = generate_positive_samples()
    generate_negative_samples(positives["canonical"])


if __name__ == "__main__":
    main()
