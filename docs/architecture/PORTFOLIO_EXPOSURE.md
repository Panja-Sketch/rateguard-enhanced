# Portfolio Blast Radius & Financial Exposure Specification

## 1. Overview & Purpose
The **Portfolio Exposure Analyzer** evaluates `ImpactPredicate` filters directly against synthetic policies, reprices affected populations through `PremiumOracle` and `IPIRRatingTarget`, and aggregates exact financial exposure metrics using Python `Decimal` arithmetic.

---

## 2. Population Classifications
1. **Total Portfolio Population:** Total policies analyzed (e.g. 50,000).
2. **Semantic Exposed Population:** Policies whose risk attributes match `ImpactPredicate` boundaries (e.g. roof age 21–30 or effective date Sep 1–14).
3. **Behavioral Affected Population:** Policies whose execution trace steps diverge between canonical and target engines.
4. **Financially Affected Population:** Policies whose final billed premium differs (`expected_premium != actual_premium`).

---

## 3. Financial Metrics & Deduplication
- **Undercharge & Overcharge Tracking:** Identifies undercharged policies (`actual < expected`) and overcharged policies (`actual > expected`) separately.
- **Multi-Defect Deduplication:** Tracks policy-level mappings (`policy_id → set(issue_ids)`) to prevent double-counting overall portfolio exposure across overlapping defects.
- **Exact Decimal Arithmetic:** All monetary aggregations utilize exact `Decimal` values.

