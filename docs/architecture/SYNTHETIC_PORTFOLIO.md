# Synthetic 50,000 Policy Portfolio Specification

## 1. Overview & Purpose
The **Synthetic Portfolio Generator** produces a reproducible dataset of 50,000 policies (`az_ho3_2026_synthetic_50k.csv`) to evaluate portfolio-wide semantic blast radius, trace divergence, and financial exposure for the Arizona HO3 rate plan.

---

## 2. Privacy & PII Guarantee
The dataset is 100% synthetic generated with a fixed seed (`seed = 42`). It contains zero Personally Identifiable Information (PII) — no names, addresses, SSNs, phone numbers, or email addresses.

---

## 3. Risk Distributions
- **Territory:** Uniform distribution across `T01` to `T20`.
- **Roof Age:** 0 to 60+ years, with a ~20% population in the defective `21..30` range.
- **Deductible:** Weighted across $500, $1,000, $2,500, and $5,000.
- **Construction Type:** Frame (40%), Masonry (45%), Superior (15%).
- **Effective Dates:** Spans September 1, 2026 to December 31, 2026, including ~12% in the September 1–14 temporal discrepancy window.
- **Transaction Types:** New Business (70%), Renewal (30%).

