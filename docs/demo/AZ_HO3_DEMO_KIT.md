# RateGuard AI — Sample Assets & Acceptance Test Kit

This directory contains clean, unencumbered synthetic demo assets and configuration templates for verifying RateGuard AI's **Assurance Mission V2** workflows without private carrier data.

---

## 1. Demo Asset Inventory (`data/demo/`)

| Asset File | Description | Purpose |
| :--- | :--- | :--- |
| [`data/implementations/canonical/AZ_HO3_2026_09_ipir.json`](../../data/implementations/canonical/AZ_HO3_2026_09_ipir.json) | Authoritative Arizona Homeowners HO3 Pricing Intent AST | Canonical filing intent baseline |
| [`data/implementations/defective/AZ_HO3_2026_09_ipir.json`](../../data/implementations/defective/AZ_HO3_2026_09_ipir.json) | Defective Rating Engine Implementation AST | Multi-defect regression testing |
| [`data/portfolio/az_ho3_2026_synthetic_50k.csv`](../../data/portfolio/az_ho3_2026_synthetic_50k.csv) | 50,000 synthetic in-force Arizona policy risk records | Portfolio exposure & financial blast radius math |

> **Synthetic Notice**: All rates, factors, boundaries, and policy records are synthetic actuarial representations created exclusively for demonstration and software verification.

---

## 2. Modes Covered by Kit

1. **`EQUIVALENCE`**: Symmetric comparison of Source A ↔ Source B ASTs — neither side is presumed authoritative, and no directional patch is generated during the mission itself; one can be generated on demand, in either direction, from the Alignment Options tab.
2. **`RELEASE_CONFORMANCE`**: Compares Authoritative Intent against Target Rating Implementation.

