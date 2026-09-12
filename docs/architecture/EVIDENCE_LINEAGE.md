# Evidence Lineage & Firestore Persistence Specification

## 1. Overview & Purpose
RateGuard AI records typed evidence lineage records throughout the Mission V2 assurance pipeline to guarantee end-to-end auditability from raw rate filings to executive deployment decisions.

---

## 2. Storage Adapters & Selection
RateGuard supports storage selection via the `RATEGUARD_RUN_STORE` environment variable:

- `RATEGUARD_RUN_STORE=memory` (Default): Uses `InMemoryRunStore` for fast, isolated unit tests.
- `RATEGUARD_RUN_STORE=firestore`: Uses `FirestoreRunStore` connecting to Google Cloud Firestore project `rateguard-ai` using Application Default Credentials (ADC).

---

## 3. Firestore Schema Hierarchy
```text
assurance_runs/{run_id}
├── events/{event_id}
└── evidence/{evidence_id}
```

---

## 4. Evidence Lineage Types
1. `SOURCE`: Reference to original actuarial filing / spec.
2. `IPIR_PACKAGE`: Serialized canonical or target IPIR package.
3. `SEMANTIC_DIFF`: Granular semantic diff report.
4. `IMPACT_ANALYSIS`: DAG impact paths and risk predicates.
5. `TEST_PLAN`: Risk-directed scenario plan.
6. `EXECUTION_RESULT`: Expected vs actual target quote reconciliation.
7. `ROOT_CAUSE`: Root Cause Analysis (RCA) finding.
8. `PORTFOLIO_EXPOSURE`: 50,000 policy financial exposure summary.
9. `ASSURANCE_DECISION`: Final evidence-backed recommendation.

