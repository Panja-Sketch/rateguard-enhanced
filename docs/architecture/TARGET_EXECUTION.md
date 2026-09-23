# Target Rating Engine Execution Specification

## 1. Overview & Purpose
The **Target Rating Engine** (`IPIRRatingTarget`) represents external rating systems (e.g. policy administration systems, external rating microservices, legacy rating engines) in RateGuard AI's assurance framework.

---

## 2. Core Architectural Principles
1. **Vendor Neutrality (by contract, not by built adapter):** Implements a generic `RatingTarget` interface. Today the only implemented, tested adapter is the versioned REST rating-engine connector (`backend/app/connectors/`) calling `backend/rating_engine`. Any REST target that implements the same connector contract can be registered by an administrator without mutating reconciliation logic — this includes an engine fronting Guidewire, Duck Creek, or a legacy platform, but no such adapter has been built or tested against a real Guidewire or Duck Creek deployment.
2. **Defect Encapsulation:** The target engine contains zero hard-coded conditionals (`if roof_age >= 21: use 1.25`). All defective behaviors are loaded purely from the target IPIR package (`AZ_HO3_2026_09_DEFECTIVE`).
3. **Independent Execution:** Executes the target rate plan independently and produces a strongly typed `TargetQuoteResult` containing monetary output and step-by-step trace data.

