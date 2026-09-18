"""Source-ingestion adapters and compilers.

`app.ingestion.workbook_v1` is the Controlled RateGuard Workbook v1 compiler
(locked doc section 5). It is independent of the legacy `app.adapters.excel`
stub (which is out of scope for this change -- see docs/implementation/
DECISIONS.md D1 for the tracked, not-yet-executed removal of that reachable
surface).
"""
