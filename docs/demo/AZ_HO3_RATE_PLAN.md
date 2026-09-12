# Arizona Homeowners HO3 Synthetic Rate Plan Specification (`AZ_HO3_2026_09`)

## 1. Executive Summary & Purpose
This document specifies the **canonical synthetic rate plan** (`AZ_HO3_2026_09`) for RateGuard AI's hackathon demonstration.

> [!NOTE]
> **Synthetic Dataset Disclaimer:** This pricing plan is 100% synthetic data authored for testing and verification purposes. It does not represent any real insurance company's rate filing or proprietary actuarial formulas.

This plan serves as the authoritative ground-truth pricing intent ($IPIR_{\text{canonical}}$). In subsequent demonstration stages, RateGuard will independently compare this canonical intent against an intentionally defective runtime implementation where a pricing factor is corrupted.

---

## 2. Plan Metadata & Product Scope
- **Package ID:** `AZ_HO3_2026_09`
- **Package Name:** `Arizona Homeowners HO3 Synthetic Rate Plan`
- **Version:** `2026.09`
- **Effective Date:** `2026-09-01` (No expiration date)
- **Supported Transactions:** `NEW_BUSINESS`, `RENEWAL`
- **Jurisdiction:** United States (`US`), Arizona (`AZ`)
- **Line of Business:** `HOMEOWNERS` (Form `HO3`)

---

## 3. Rating Inputs & Variables

| Input ID | Name | Data Type | Range / Allowed Values | Unit |
| :--- | :--- | :--- | :--- | :--- |
| `territory` | Territory Code | `CATEGORY` | `T01` through `T20` | N/A |
| `roof_age` | Age of Roof | `INTEGER` | `0` to `80` | Years |
| `deductible` | Policy Deductible | `MONEY` | `500`, `1000`, `2500`, `5000` | USD ($) |
| `protection_class` | Public Protection Class | `INTEGER` | `1` to `10` | Index |
| `construction_type` | Construction Type | `CATEGORY` | `FRAME`, `MASONRY`, `SUPERIOR` | N/A |
| `dwelling_limit` | Dwelling Limit (Cov A) | `MONEY` | `$100,000` to `$2,000,000` | USD ($) |
| `multi_policy` | Multi-Policy Discount | `BOOLEAN` | `true`, `false` | Flag |
| `claims_free` | Claims-Free Status | `BOOLEAN` | `true`, `false` | Flag |

---

## 4. Rating Constants, Tables, & Factors

### 4.1 Base Rate & Constants
- **Base Rate (`base_rate`):** `$650.00`
- **Minimum Policy Premium (`policy_minimum`):** `$575.00`
- **Policy Fee (`policy_fee`):** `$25.00`

### 4.2 1-Dimensional Factor Tables
1. **`territory_factor` (Exact Match):**
   - `T01` (0.88) ... `T07` (1.00) ... `T17` (**1.20**) ... `T20` (1.26). Increments by 0.02 per territory.
2. **`roof_age_factor` (Range Bracket):**
   - `0–5` yrs: `0.90`
   - `6–10` yrs: `0.95`
   - `11–20` yrs: `1.10`
   - **`21–30` yrs: `1.35`** *(Canonical target factor for future demo drift detection)*
   - `31–40` yrs: `1.50`
   - `41–80` yrs: `1.70`
3. **`deductible_factor` (Exact Match):**
   - `$500`: `1.15`, `$1,000`: `1.00`, `$2,500`: `0.85`, `$5,000`: `0.72`
4. **`protection_class_factor` (Range Bracket):**
   - Class `1–2`: `0.92`, `3–4`: `0.96`, `5–6`: `1.00`, `7–8`: `1.08`, `9–10`: `1.18`
5. **`construction_factor` (Exact Match):**
   - `FRAME`: `1.10`, `MASONRY`: `0.95`, `SUPERIOR`: `0.88`
6. **`dwelling_limit_factor` (Range Bracket):**
   - `$100k–$249.9k`: `0.85`, `$250k–$399.9k`: `1.00`, `$400k–$599.9k`: `1.15`, `$600k–$999.9k`: `1.35`, `$1M+`: `1.60`

### 4.3 2-Dimensional Adjustment Table
- **`territory_construction_adjustment` (2D Exact Match: Territory x Construction Type):**
  - Fully enumerates all 60 combinations of 20 territories and 3 construction types.
  - Highlights specific high-risk interactions:
    - `T17 + FRAME` = `1.08`, `T18 + FRAME` = `1.09`, `T19 + FRAME` = `1.10`, `T20 + FRAME` = `1.12`
    - `T17 + MASONRY` = `1.02`, `T18 + MASONRY` = `1.03`, `T19 + MASONRY` = `1.04`, `T20 + MASONRY` = `1.05`
    - All `SUPERIOR` construction = `0.98`
    - Remaining combinations = `1.00`

---

## 5. Discounts, Constraints, & Fees

1. **`multi_policy_discount` (Sequence 1):** `12%` discount (`0.12`) applied to `gross_risk_premium` when `multi_policy == true`.
2. **`claims_free_discount` (Sequence 2):** `5%` discount (`0.05`) applied to `gross_risk_premium` when `claims_free == true`.
3. **`policy_minimum` (Sequence 3):** Enforces minimum premium bound of `$575.00` on `premium_after_discounts`.
4. **`policy_fee` (Sequence 4):** Flat `$25.00` policy fee added after minimum premium enforcement.

---

## 6. Calculation Execution DAG & Formula

### Compact Formula

$$\text{Gross Risk Premium} = \text{base\_rate} \times \prod \text{factors}$$

$$\text{Premium After Discounts} = \text{Gross Risk Premium} \times (1 - \text{disc}_{\text{multi}}) \times (1 - \text{disc}_{\text{claims}})$$

$$\text{Premium After Minimum} = \max(\text{Premium After Discounts}, \$575.00)$$

$$\text{Final Billed Premium} = \text{Premium After Minimum} + \$25.00$$

---

## 7. Crucial Hackathon Surface Areas

1. **Roof Age 21 Boundary Surface:**
   - In this canonical plan, `roof_age` in bracket `21–30` yields factor **`1.35`**.
   - *Future Defect Note:* The target defective rating implementation will erroneously contain factor `1.25` for `roof_age >= 21`. RateGuard's semantic diff engine, test generator, and independent oracle will detect and pinpoint this exact discrepancy.
2. **Territory T17 Interaction Surface:**
   - Territory `T17` has base factor `1.20` and 2D interaction factor `1.08` with `FRAME` construction.
3. **No Defects Present:** This canonical package contains **zero defects** and represents 100% correct pricing intent.

