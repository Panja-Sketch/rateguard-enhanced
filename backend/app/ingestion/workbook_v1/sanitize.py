"""Formula-injection / stored-XSS defense on export (locked doc section
15.3: "Sanitize values before displaying them to prevent stored XSS or
spreadsheet-formula injection on export").

Applied at the boundary where a workbook-derived string (a metadata value,
or an input/table/calculation/output `name`) is turned into a receipt or
IPIR string field -- never to identifiers (`id` fields), which must remain
byte-for-byte exact for reference resolution.
"""

import html

# The well-known CSV/XLSX "formula injection" mitigation: a value beginning
# with any of these characters can be reinterpreted as a formula by Excel,
# Google Sheets, or LibreOffice Calc on re-import/export.
_CSV_INJECTION_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_for_display(value: str) -> str:
    """HTML-escapes a workbook-derived string before it is ever rendered in
    a browser. Idempotent (escaping an already-escaped string only doubles
    literal ampersands, never reintroduces raw markup)."""
    return html.escape(value, quote=True)


def sanitize_for_export(value: str) -> str:
    """Neutralizes spreadsheet-formula injection: a value beginning with
    '=', '+', '-', '@', a tab, or a carriage return is prefixed with a
    leading apostrophe, which Excel/Sheets/LibreOffice treat as "force
    text" -- the value can never be reinterpreted as a formula if the
    receipt/IPIR content is later re-exported into a CSV or XLSX cell."""
    if value and value[0] in _CSV_INJECTION_PREFIXES:
        return "'" + value
    return value


def sanitize_display_and_export(value: str) -> str:
    """Applies both mitigations, for a value that may be both displayed in
    the UI and later re-exported (e.g. a receipt metadata field)."""
    return sanitize_for_display(sanitize_for_export(value))
