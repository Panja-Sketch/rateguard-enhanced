"""Pre-openpyxl ZIP/XML safety inspection (locked doc sections 5.3 step 3 and
15.3). Every check here runs on the raw bytes using only the Python stdlib
`zipfile` and `xml.etree.ElementTree` -- never `lxml` (no external-entity
resolution risk), never `eval`/`exec`/shell/pickle/unsafe yaml. Nothing here
ever asks openpyxl, Excel, or LibreOffice to open the file.

Every rejection raises `WorkbookRejectionError` with a distinct `.code` so a
caller/test can assert on the specific reason a workbook was rejected.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET

from app.ingestion.workbook_v1.errors import WorkbookErrorDetail, WorkbookRejectionError
from app.ingestion.workbook_v1.limits import (
    MAX_ENTRY_COMPRESSION_RATIO,
    MAX_TOTAL_UNCOMPRESSED_BYTES,
    MAX_UPLOAD_BYTES,
    MAX_ZIP_ENTRIES,
    MIN_ENTRY_SIZE_FOR_RATIO_CHECK,
    OLE_CFB_SIGNATURE,
    REQUIRED_EXTENSION,
    ZIP_LOCAL_FILE_SIGNATURES,
)

_RELS_NAMESPACE_SUFFIX = "}Relationship"
_CONTENT_TYPES_ENTRY = "[Content_Types].xml"
_VBA_PROJECT_ENTRY = "xl/vbaProject.bin"
_EXTERNAL_LINKS_PREFIX = "xl/externalLinks/"
_EMBEDDINGS_PREFIX = "xl/embeddings/"

# Content-type substrings that indicate macro-enabled or VBA-bearing OOXML
# parts (ECMA-376 / OPC part conventions). Checked case-sensitively against
# the real, well-known strings Microsoft Office writes.
_MACRO_CONTENT_TYPE_MARKERS = ("macroEnabled", "ms-excel.sheet.macroEnabled")
_VBA_CONTENT_TYPE_MARKER = "vbaProject"
_OLE_OBJECT_RELATIONSHIP_MARKER = "oleObject"
_EXTERNAL_LINK_RELATIONSHIP_MARKER = "externalLink"


def _reject(code: str, message: str, **detail_kwargs) -> None:
    detail = WorkbookErrorDetail(**detail_kwargs) if detail_kwargs else None
    raise WorkbookRejectionError(code=code, message=message, details=[detail] if detail else [])


def validate_upload_basics(content: bytes, filename: str) -> None:
    """Extension, size, and file-signature checks that must pass before any
    ZIP or XML parser is ever invoked on the bytes."""
    if not filename.lower().endswith(REQUIRED_EXTENSION):
        _reject(
            "INVALID_FILE_EXTENSION",
            f"Only {REQUIRED_EXTENSION} files are accepted; got '{filename}'.",
            note=filename,
        )

    if len(content) > MAX_UPLOAD_BYTES:
        _reject(
            "FILE_TOO_LARGE",
            f"File is {len(content)} bytes, exceeding the {MAX_UPLOAD_BYTES}-byte "
            "(10 MiB) limit (locked doc section 4.1.B).",
        )

    if len(content) == 0:
        _reject("CORRUPT_ARCHIVE", "Uploaded file is empty.")

    if content.startswith(OLE_CFB_SIGNATURE):
        # A genuine encrypted/password-protected .xlsx is stored as an OLE
        # Compound File Binary container (the real ZIP is wrapped inside an
        # encryption stream) -- this signature is the byte-level ground
        # truth for "this is Microsoft's standard encrypted OOXML wrapper",
        # not a heuristic.
        _reject(
            "ENCRYPTED_WORKBOOK",
            "Workbook appears to be password-protected/encrypted (OLE Compound "
            "File Binary container signature detected instead of a ZIP archive).",
        )

    if not content.startswith(ZIP_LOCAL_FILE_SIGNATURES):
        _reject(
            "INVALID_FILE_SIGNATURE",
            "File does not have a valid ZIP/XLSX magic-byte signature.",
        )


def _check_entry_names(names: list[str]) -> None:
    for name in names:
        if "\\" in name:
            _reject(
                "PATH_TRAVERSAL_ENTRY",
                f"ZIP entry name '{name}' contains a backslash (possible drive-relative escape).",
                note=name,
            )
        pure = PurePosixPath(name)
        if pure.is_absolute() or ".." in pure.parts:
            _reject(
                "PATH_TRAVERSAL_ENTRY",
                f"ZIP entry name '{name}' attempts path traversal or is an absolute path.",
                note=name,
            )


def _check_entry_counts_and_ratios(infos: list[zipfile.ZipInfo]) -> None:
    if len(infos) > MAX_ZIP_ENTRIES:
        _reject(
            "EXCESSIVE_ZIP_ENTRIES",
            f"Archive contains {len(infos)} entries, exceeding the {MAX_ZIP_ENTRIES}-entry limit.",
        )

    total_uncompressed = sum(info.file_size for info in infos)
    if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_BYTES:
        _reject(
            "ZIP_BOMB_SUSPECTED",
            f"Archive would decompress to {total_uncompressed} bytes, exceeding the "
            f"{MAX_TOTAL_UNCOMPRESSED_BYTES}-byte total-uncompressed-size limit.",
        )

    for info in infos:
        if info.file_size < MIN_ENTRY_SIZE_FOR_RATIO_CHECK:
            continue
        compressed = max(info.compress_size, 1)
        ratio = info.file_size / compressed
        if ratio > MAX_ENTRY_COMPRESSION_RATIO:
            _reject(
                "ZIP_BOMB_SUSPECTED",
                f"ZIP entry '{info.filename}' has a compression ratio of {ratio:.1f}x, "
                f"exceeding the {MAX_ENTRY_COMPRESSION_RATIO}x per-entry limit.",
                note=info.filename,
            )


def _check_active_content(names: set[str], zf: zipfile.ZipFile) -> None:
    if _VBA_PROJECT_ENTRY in names:
        _reject(
            "VBA_PROJECT_PRESENT",
            "Workbook contains a VBA project (xl/vbaProject.bin); macros are never executed or accepted.",
            note=_VBA_PROJECT_ENTRY,
        )

    if _CONTENT_TYPES_ENTRY in names:
        try:
            content_types_xml = zf.read(_CONTENT_TYPES_ENTRY).decode("utf-8", errors="replace")
        except (KeyError, zipfile.BadZipFile):
            content_types_xml = ""
        if any(marker in content_types_xml for marker in _MACRO_CONTENT_TYPE_MARKERS):
            _reject(
                "MACRO_ENABLED_WORKBOOK",
                "[Content_Types].xml declares a macro-enabled content type.",
                sheet=_CONTENT_TYPES_ENTRY,
            )
        if _VBA_CONTENT_TYPE_MARKER in content_types_xml:
            _reject(
                "VBA_PROJECT_PRESENT",
                "[Content_Types].xml declares a VBA project content type.",
                sheet=_CONTENT_TYPES_ENTRY,
            )

    if any(name.startswith(_EXTERNAL_LINKS_PREFIX) for name in names):
        _reject(
            "EXTERNAL_LINK_PRESENT",
            "Workbook contains an external-links part (xl/externalLinks/...).",
        )

    if any(name.startswith(_EMBEDDINGS_PREFIX) for name in names):
        _reject(
            "OLE_EMBEDDED_OBJECT",
            "Workbook contains an embedded-object part (xl/embeddings/...).",
        )

    for name in names:
        if name.endswith(".bin") and name != _VBA_PROJECT_ENTRY and _EMBEDDINGS_PREFIX not in name:
            # A stray OLE .bin part outside the known VBA/embeddings
            # locations is still active/opaque binary content this
            # contract does not support opening.
            _reject(
                "OLE_EMBEDDED_OBJECT",
                f"Workbook contains an unexpected OLE binary part '{name}'.",
                note=name,
            )


def _check_relationships(names: set[str], zf: zipfile.ZipFile) -> None:
    """Parses every `.rels` part with stdlib `xml.etree.ElementTree` (which
    performs no external-entity resolution) looking for unsafe relationship
    targets: absolute paths, non-empty URL schemes, path traversal, or
    `TargetMode="External"` outside the already-handled external-links case.
    """
    rels_entries = [n for n in names if n.endswith(".rels")]
    for rels_name in rels_entries:
        try:
            raw = zf.read(rels_name)
            root = ET.fromstring(raw)  # noqa: S314 - stdlib ET, no external entity resolution
        except ET.ParseError:
            _reject(
                "CORRUPT_ARCHIVE",
                f"Relationship part '{rels_name}' is not well-formed XML.",
                sheet=rels_name,
            )
            return

        for elem in root.iter():
            if not elem.tag.endswith(_RELS_NAMESPACE_SUFFIX):
                continue
            rel_type = elem.get("Type", "")
            target = elem.get("Target", "")
            target_mode = elem.get("TargetMode", "Internal")

            if _OLE_OBJECT_RELATIONSHIP_MARKER in rel_type:
                _reject(
                    "OLE_EMBEDDED_OBJECT",
                    f"Relationship '{elem.get('Id')}' in '{rels_name}' targets an OLE object.",
                    sheet=rels_name,
                )

            if target_mode == "External":
                if _EXTERNAL_LINK_RELATIONSHIP_MARKER.lower() in rel_type.lower():
                    _reject(
                        "EXTERNAL_LINK_PRESENT",
                        f"Relationship '{elem.get('Id')}' in '{rels_name}' is an external link.",
                        sheet=rels_name,
                    )
                _reject(
                    "UNSAFE_RELATIONSHIP",
                    f"Relationship '{elem.get('Id')}' in '{rels_name}' has TargetMode=External "
                    f"to '{target}', which is outside the allowed external-links handling.",
                    sheet=rels_name,
                    note=target,
                )

            # A leading "/" is normal, safe OPC convention for a
            # package-root-relative internal part reference (e.g.
            # "/xl/worksheets/sheet1.xml", which openpyxl itself writes) --
            # it never escapes the ZIP. Only genuine path traversal (".."),
            # not a bare leading slash, is unsafe here.
            if ".." in PurePosixPath(target).parts:
                _reject(
                    "UNSAFE_RELATIONSHIP",
                    f"Relationship '{elem.get('Id')}' in '{rels_name}' targets a "
                    f"path-traversing location '{target}'.",
                    sheet=rels_name,
                    note=target,
                )
            if len(target) >= 2 and target[1] == ":" and target[0].isalpha():
                _reject(
                    "UNSAFE_RELATIONSHIP",
                    f"Relationship '{elem.get('Id')}' in '{rels_name}' targets a drive-qualified "
                    f"path '{target}'.",
                    sheet=rels_name,
                    note=target,
                )
            if "://" in target and target_mode != "External":
                _reject(
                    "UNSAFE_RELATIONSHIP",
                    f"Relationship '{elem.get('Id')}' in '{rels_name}' targets a URL '{target}' "
                    "without declaring TargetMode=External.",
                    sheet=rels_name,
                    note=target,
                )


def validate_archive_safety(content: bytes) -> zipfile.ZipFile:
    """Runs every ZIP/XML structural safety check and returns the opened
    `zipfile.ZipFile` for the caller to hand to openpyxl (so the archive is
    only opened once). Raises `WorkbookRejectionError` on the first
    violation found; callers must not continue past that point."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        _reject("CORRUPT_ARCHIVE", f"File is not a valid ZIP/XLSX archive: {exc}")
        raise  # unreachable, keeps type-checkers happy

    infos = zf.infolist()
    names = [info.filename for info in infos]

    _check_entry_counts_and_ratios(infos)
    _check_entry_names(names)
    _check_active_content(set(names), zf)
    _check_relationships(set(names), zf)

    return zf
