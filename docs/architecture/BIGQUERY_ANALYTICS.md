# BigQuery Portfolio Persistence & Analytics Architecture

## 1. Overview
RateGuard AI integrates Google BigQuery for cloud-scale portfolio financial exposure analysis across synthetic and production policy datasets. BigQuery complements the local CSV repository (`LocalPortfolioRepository`), adhering to a common interface (`BasePortfolioRepository`).

```text
Local Portfolio CSV (50K Policies)
           │
           ├────────────────────────┐
           ▼                        ▼
LocalPortfolioRepository   BigQueryPortfolioRepository
           │                        │
           └────────────┬───────────┘
                        ▼
            BasePortfolioRepository
                        │
                        ▼
              PortfolioAnalyzer
                        │
                        ▼
             Financial Exposure Result
```

## 2. BigQuery Dataset & Tables

- **Project:** `rateguard-ai`
- **Dataset:** `rateguard` (Location: `US`)
- **Portfolio Table:** `rateguard.synthetic_policies`
  - `policy_id` (STRING)
  - `product_id` (STRING)
  - `state` (STRING)
  - `form` (STRING)
  - `transaction_type` (STRING)
  - `effective_date` (DATE)
  - `territory` (STRING)
  - `roof_age` (INT64)
  - `deductible` (INT64)
  - `protection_class` (INT64)
  - `construction_type` (STRING)
  - `dwelling_limit` (INT64)
  - `multi_policy` (BOOL)
  - `claims_free` (BOOL)
  - `claims_free_years` (INT64)
  - `canonical_premium` (NUMERIC)
- **Results Table:** `rateguard.portfolio_exposure_results`
  - `run_id` (STRING)
  - `timestamp` (TIMESTAMP)
  - `total_policies` (INT64)
  - `exposed_policies` (INT64)
  - `behaviorally_affected` (INT64)
  - `financially_affected` (INT64)
  - `expected_premium` (NUMERIC)
  - `target_premium` (NUMERIC)
  - `signed_variance` (NUMERIC)
  - `absolute_variance` (NUMERIC)
  - `decision` (STRING)

## 3. SQL Injection Prevention & Parameterization
`sql_translator.py` translates `ImpactPredicate` models into safe `ScalarQueryParameter` instances for BigQuery, supporting parameterized operators (`=`, `!=`, `>`, `>=`, `<`, `<=`) without raw string concatenation.

## 4. Operational Scripts
- `python scripts/setup_bigquery.py`: Idempotently provisions dataset and table schemas.
- `python scripts/upload_synthetic_portfolio_bigquery.py`: Uploads seed-42 50,000 synthetic policies into BigQuery.
- `python scripts/verify_bigquery_portfolio_parity.py`: Verifies 100% financial and exposure metric parity between CSV and BigQuery.

