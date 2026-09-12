# Independent Premium Oracle Specification & Architecture

## 1. Overview & Purpose
The **Independent Premium Oracle** is RateGuard AI's authoritative, deterministic calculation engine.

It evaluates canonical or target IPIR (`Insurance Pricing Intermediate Representation`) rate plans against risk inputs to compute exact expected premiums and step-by-step calculation traces.

---

## 2. Core Architectural Principles

1. **Deterministic Software Authority:** All premium calculations, factor multiplications, modifier adjustments, bounds checks, and fee additions are performed by deterministic Python software using arbitrary-precision `Decimal` arithmetic.
2. **Zero LLM Arithmetic:** LLMs and Gemini agents are **never** permitted to calculate, guess, or override numerical premium results. LLMs handle document parsing, semantic mapping, and natural-language explanations, while the Oracle computes ground-truth numbers.
3. **Strict Decimal Precision:** Binary floating-point arithmetic (`float`) is strictly prohibited in calculation paths to prevent silent floating-point representation drift.
4. **Complete Calculation Audit Tracing:** Every evaluation produces a strongly typed `PremiumTrace` containing ordered `TraceStep` records with inputs, operation type, output results, and provenance metadata.
5. **Deterministic Failures for Unsupported Semantics:** Unmodeled features, ambiguous rate table lookups, dependency cycles, or missing inputs raise explicit, diagnostic `OracleError` exceptions rather than making silent assumptions.

---

## 3. Execution Pipeline Overview

```text
[ RiskInput + Effective Date ] 
              ↓
  1. Validate Risk Inputs (InputDataType, allowed_values, min/max)
              ↓
  2. Resolve Active Constants (base_rate, minimums, fees)
              ↓
  3. Resolve Active Rate Tables (1D Exact/Range, 2D Exact)
              ↓
  4. Evaluate Active Rules (IF / THEN / ELSE AST)
              ↓
  5. Topological Sort & Execute Calculation Nodes (AST Expressions & Rounding)
              ↓
  6. Apply Active Modifiers (Percentage & Flat Discounts/Surcharges in sequence)
              ↓
  7. Enforce Active Constraints (Minimum / Maximum Bounds)
              ↓
  8. Apply Active Fees (Policy Fees)
              ↓
  9. Resolve Target Output (final_policy_premium & OracleResult)
```

---

## 4. Subsystem Details

### 4.1 Rate Table Lookup
- Supports **1D Exact**, **1D Range** (bracket matching with minimum/maximum inclusivity), and **2D Exact** lookups.
- Enforces strict uniqueness: exactly one matching row is required. Zero matches or multiple matches raise `TableLookupError`.

### 4.2 Calculation Ordering (DAG)
- Uses NetworkX topological sorting on `CalculationNode.depends_on` graphs.
- Cycle detection: Any circular dependency raises `ExpressionEvaluationError`.

### 4.3 Rounding & Decimal Math
- Per-node rounding rules use `Decimal.quantize` with `RoundingMode` (`HALF_UP`, `HALF_EVEN`, `FLOOR`, `CEILING`).

### 4.4 Modifiers, Constraints, & Fees
- **Modifiers:** Sequence-ordered percentage/flat discounts and surcharges evaluated against source calculation nodes.
- **Constraints:** Enforces minimum or maximum premium bounds (e.g. `$575.00` policy minimum).
- **Fees:** Applies flat transaction/policy fees (e.g. `$25.00` policy fee).

---

## 5. Error Hierarchy

- `OracleError`: Base exception for all engine failures.
  - `InputValidationError`: Missing/invalid risk inputs or range violations.
  - `TableLookupError`: Zero or multiple matching table rows.
  - `ExpressionEvaluationError`: Division by zero or AST invalidity.
  - `ConditionEvaluationError`: Relational/logical condition evaluation errors.
  - `ReferenceResolutionError`: Nonexistent node ID referenced.
  - `EffectiveDateError`: Inactive rate plan or component on calculation date.
  - `UnsupportedIPIRFeatureError`: Feature beyond IPIR 0.1 scope.
