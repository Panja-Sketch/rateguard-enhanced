# Source-Agnostic Pricing Adapters Architecture

## Executive Summary

RateGuard AI's canonical target design is source-agnostic: insurers maintain rating logic in heterogeneous formats (actuarial Excel workbooks, regulatory PDF filings, structured JSON rate specifications, legacy core system configurations), all compiling into a single, strongly typed, vendor-neutral **Insurance Pricing Intermediate Representation (IPIR 0.1)**.

**Deployed status:** only the `STRUCTURED_JSON` adapter is exposed through the live API today, with strict schema validation (`extra="forbid"`) and a structured `422` on any unrecognized field — see the README's [Supported Source Format: JSON Schema](../../README.md#supported-source-format-json-schema). Excel and PDF adapter code exists in `backend/app/adapters/` but is **not** reachable from `POST /api/v1/sources`: `PricingSourceIngestionService.register_source` rejects `.xlsx`/`.xls`/`.pdf` uploads outright (HTTP `400`), because their extraction accuracy against real filings has not been verified end-to-end. RateGuard will not claim a compilation it can't stand behind.

---

## Adapter Formats

| Format Identifier | Source Type | Adapter Implementation Class | Status |
| :--- | :--- | :--- | :--- |
| `STRUCTURED_JSON` | Structured Actuarial JSON Spec | `StructuredJSONPricingAdapter` | **Live** — direct 1:1 mapping, deterministic compilation |
| `EXCEL` | Actuarial Workbook (`.xlsx`) | `ExcelPricingAdapter` | Code exists, not exposed via the API |
| `PDF` | Rate Filing Specification (`.pdf`) | `PDFPricingAdapter` | Code exists, not exposed via the API |
| `PLATFORM_CONFIG` | Synthetic Rating Engine Config | `PlatformConfigAdapter` | Auto-detected from JSON structure, compiled deterministically |

---

## Adapter Architecture Principles

1. **Vendor Neutrality**: No vendor-specific constructs in IPIR. Vendor details are isolated inside `Provenance.sources`.
2. **Deterministic Computation**: Deterministic Python code computes factor lookup arrays and formula trees; Gemini is never in the arithmetic path for any format.
3. **Traceable Provenance**: Every extracted element links directly back to its source location.
4. **Fail closed, not silently**: A source that doesn't compile is rejected with a structured error, never accepted with fabricated or approximated content.
