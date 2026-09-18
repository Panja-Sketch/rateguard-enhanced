"""Controlled RateGuard Workbook v1 compiler (locked doc section 5).

Compiles a `.xlsx` file conforming to the locked sheet contract into an IPIR
v0.2 package (`app.ipir.v0_2`), producing a `CompilationReceipt` that reports
exactly what was found, what was rejected, and why -- never a silent partial
success (locked doc sections 2.3 and 17.3, "no false PASS").

Entry point: `app.ingestion.workbook_v1.compiler.compile_workbook`.

Explicitly out of scope for this module (locked doc section 5.4 and the
session instructions that built it): Gemini-assisted header mapping, and any
autonomous retry loop that edits a mapping until control cases pass. This
compiler only accepts the exact locked column contract; an unrecognized
header is a hard rejection, not a suggestion.
"""
