"""IPIR v0.2 — the locked, authoritative contract for new Controlled Workbook v1
and REST-connector flows (see docs/architecture/RATEGUARD_LOCKED_SOURCE_OF_TRUTH.md
section 6). Built as a parallel module alongside `app.ipir` (v0.1), which
remains available for backward compatibility. See
docs/implementation/DECISIONS.md (D2) for why this is parallel rather than an
in-place rewrite, and `app.ipir.v0_2.compat` for the lowering boundary that
lets the existing v0.1 deterministic engines evaluate v0.2 packages without a
second pricing-engine implementation.
"""
