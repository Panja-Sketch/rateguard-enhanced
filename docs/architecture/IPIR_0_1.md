# Insurance Pricing Intermediate Representation (IPIR) 0.1 Specification

## 1. Overview & Purpose
**IPIR (Insurance Pricing Intermediate Representation)** is a canonical, executable, vendor-neutral, and source-agnostic Abstract Syntax Tree (AST) schema for insurance pricing logic.

Insurance pricing specifications move across disparate media and vendor runtimes (regulatory filings, actuarial spreadsheets, Guidewire, Duck Creek, Earnix, custom REST rating APIs, production engines). IPIR acts as RateGuard's universal core abstraction, isolating rate intent and pricing semantics from implementation technologies.

---

## 2. Core Design Principles

1. **Vendor-Neutral & Source-Agnostic:** IPIR models pricing concepts (rate tables, conditions, expressions, modifiers, rounding) without hardcoding vendor-specific constructs (such as Guidewire Gosu, Duck Creek XML, or Excel cell references). Vendor metadata is kept strictly in provenance attributes.
2. **Bidirectional Semantic Comparison:** IPIR is designed to allow any pricing representation $IPIR_A$ to be compared against any other representation $IPIR_B$ (e.g., Regulatory Filing ↔ Actuarial Workbook, Actuarial Workbook ↔ Guidewire Configuration).
3. **Deterministic Math & Decimal Precision:** Authoritative pricing values, money, factors, percentages, and constants strictly use arbitrary-precision Python `Decimal` data types. Binary floating-point representation (`float`) is prohibited to eliminate rounding drift.
4. **Deterministic Execution Philosophy:** Gemini (via the Google GenAI SDK) assists in prioritizing differences and explaining findings, but **never** executes authoritative premium arithmetic. IPIR is evaluated solely by deterministic software engines.
5. **Auditable Lineage & Provenance:** Every rate factor, rule, and calculation node retains metadata linking back to source filings, pages, sections, or database versions with explicit confidence metrics.

---

## 3. Scope of IPIR 0.1

### Supported Constructs in 0.1:
- Risk & policy input declarations (numeric, categorical, boolean, date)
- 1D and 2D rate lookup tables (exact matches and range brackets)
- Reusable rate constants & per-node rounding rules
- Restricted mathematical AST expressions (`ADD`, `SUBTRACT`, `MULTIPLY`, `DIVIDE`, `MIN`, `MAX`, `ROUND`)
- Relational and logical conditional rules (`IF / THEN / ELSE`)
- Percentage & flat discounts and surcharges (pricing modifiers)
- Minimum and maximum policy premium constraints
- Explicit node-level calculation dependencies (`depends_on`)
- Product line, jurisdiction, transaction context (New Business, Renewal), and effective periods
- Source provenance, confidence tracking, and canonical JSON schema generation.

### Intentionally Deferred Non-Goals for 0.1:
- 3D+ multidimensional rate tables
- Workers' compensation experience rating & retrospective rating
- Catastrophe modeling & telematics feeds
- Credit scoring & machine-learning pricing models
- Complex commercial package policies, reinsurance, and tax regimes.

---

## 4. Illustrative IPIR 0.1 Package JSON

```json
{
  "ipir_version": "0.1",
  "id": "generic_homeowners_v1",
  "name": "Generic Homeowners Rating Plan",
  "version": "1.0.0",
  "product": {
    "id": "HO3_AZ",
    "name": "Homeowners HO-3",
    "line": "HOMEOWNERS",
    "jurisdiction": {
      "country": "US",
      "state_or_province": "AZ"
    }
  },
  "effective_period": {
    "start": "2026-01-01"
  },
  "transaction_types": ["NEW_BUSINESS", "RENEWAL"],
  "inputs": [
    {
      "id": "roof_age",
      "name": "Age of Roof (Years)",
      "data_type": "INTEGER",
      "required": true,
      "minimum": 0,
      "maximum": 100
    }
  ],
  "constants": [
    {
      "id": "base_rate",
      "name": "Base Premium Rate",
      "value": "500.00"
    }
  ],
  "tables": [
    {
      "id": "roof_age_factor_table",
      "name": "Roof Age Rating Factor Table",
      "dimensions": [
        {
          "input_ref": "roof_age",
          "lookup_type": "RANGE"
        }
      ],
      "rows": [
        {
          "matches": [
            { "minimum": "0", "maximum": "10", "include_minimum": true, "include_maximum": true }
          ],
          "value": "1.00"
        },
        {
          "matches": [
            { "minimum": "11", "maximum": "20", "include_minimum": true, "include_maximum": true }
          ],
          "value": "1.10"
        },
        {
          "matches": [
            { "minimum": "21", "maximum": null, "include_minimum": true, "include_maximum": true }
          ],
          "value": "1.35"
        }
      ]
    }
  ],
  "calculations": [
    {
      "id": "total_premium",
      "name": "Total Policy Premium",
      "expression": {
        "operator": "MULTIPLY",
        "operands": [
          { "ref": "base_rate" },
          { "ref": "roof_age_factor_table" }
        ]
      },
      "depends_on": ["base_rate", "roof_age_factor_table"]
    }
  ],
  "outputs": [
    {
      "id": "final_policy_premium",
      "name": "Final Policy Premium",
      "source_ref": "total_premium",
      "currency": "USD"
    }
  ]
}
```

