# Bidirectional Semantic Diff Engine Specification

## 1. Overview & Purpose
The **Bidirectional Semantic Diff Engine** provides vendor-neutral, AST-level semantic comparison between any two `IPIRPackage` representations (`Source A` ↔ `Source B`).

Unlike text-based code diffs or schema diffs, the Semantic Diff Engine operates directly on IPIR rate plan ASTs. It matches pricing nodes, rate table dimension keys, logic conditions, modifiers, constraints, and fees by semantic keys rather than array indices or formatting.

---

## 2. Core Architectural Principles

1. **Vendor & Source Neutrality:** Operates exclusively on IPIR AST models. Works identically for Filings, Actuarial Workbooks, Guidewire implementations, Duck Creek rate plans, or custom rating engines.
2. **Bidirectional Symmetry:** Comparing `A → B` vs `B → A` identifies the same underlying semantic differences while swapping left/right values and reversing `MISSING_NODE` ↔ `EXTRA_NODE` semantics.
3. **Exact Decimal Comparison:** Numerical pricing values (factors, base rates, minimums, fees) are compared using exact Python `Decimal` values.
4. **Granular Semantic Pathing:** Pinpoints exact changed dimension cells (e.g. `tables.roof_age_factor[21..30]`) rather than reporting entire table replacements.
5. **Zero LLM Dependency for Core Diffing:** All comparisons and severity assignments are 100% deterministic software operations.

---

## 3. Difference Classification & Severity Heuristics

- **`CRITICAL`:**
  - Rating factor / table cell value changes (`TABLE_ROW_CHANGE`)
  - Execution sequence drift (`ORDER_CHANGE`)
  - Constraint amount changes (`CONSTRAINT_CHANGE`)
  - Fee amount changes (`FEE_CHANGE`)
  - Missing rating inputs or core calculation nodes (`MISSING_NODE`)
- **`HIGH`:**
  - Effective date drift (`EFFECTIVE_DATE_CHANGE`)
  - Modifier percentage/flat value changes (`MODIFIER_CHANGE`)
  - Rounding precision/mode changes (`ROUNDING_CHANGE`)
- **`MEDIUM`:**
  - Output definitions or coverage mapping adjustments (`OUTPUT_CHANGE`)
- **`LOW`:**
  - Non-pricing metadata or description changes

---

## 4. Rate Table Semantic Key Matching

Rate table rows are indexed by dimension match keys rather than line index:
- **1D Exact:** Keyed by value string (e.g. `"T17"`).
- **1D Range:** Keyed by boundary bracket (e.g. `"[21..30]"`).
- **2D Exact:** Keyed by combined dimension tuple (e.g. `"T17|FRAME"`).

