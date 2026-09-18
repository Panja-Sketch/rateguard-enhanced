"""The isolated demo rating-engine service (locked doc section 8.3): a
genuinely separate deployable target with two versioned behaviors,
`canonical-v1` ($700.00 for the golden case) and `defective-v1` ($655.00 for
the same case). Sibling package to `app`, per the locked repository
structure (docs/architecture/RATEGUARD_LOCKED_SOURCE_OF_TRUTH.md section 19)
and docs/implementation/DECISIONS.md (D3).

This session builds the service itself (the target side) only — no outbound
connector/SSRF/client code calls it yet; that is a later session's
`app.connectors` work (CP8).
"""
