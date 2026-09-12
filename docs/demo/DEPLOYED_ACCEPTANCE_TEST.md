# RateGuard AI — Deployed Acceptance Test Guide

This document provides step-by-step instructions to validate a live deployment of **RateGuard AI**.

---

## TEST 1 — CLEAN RELEASE (`RELEASE_CONFORMANCE` → `PASS`)

1. Open **Start Assurance Mission**.
2. Select **Release Conformance** mode.
3. Select Source A: `AZ_HO3_2026_09` (Authoritative Filing Intent).
4. Select Source B: `AZ_HO3_2026_09_CLEAN` (Clean Control Implementation).
5. Click **Execute Assurance Mission**.

### Expected Verification:
- `semantic_analysis.status` = `SUCCEEDED` (0 material differences).
- `experiments.match_count` = 5/5.
- `blast_radius.absolute_financial_exposure` = `$0.00`.
- `release_decision.status` = `PASS`.

---

## TEST 2 — DEFECTIVE RELEASE (`RELEASE_CONFORMANCE` → `BLOCK_DEPLOYMENT`)

1. Open **Start Assurance Mission**.
2. Select **Release Conformance** mode.
3. Select Source A: `AZ_HO3_2026_09`.
4. Select Source B: `AZ_HO3_2026_09_DEFECTIVE`.
5. Click **Execute Assurance Mission**.

### Expected Verification:
- Material semantic diffs identified (Roof factor 1.35 vs 1.25, effective date drift, fee sequence swap).
- Targeted boundary experiments reproduced calculation mismatches.
- First divergent node identified with root cause finding.
- 50K Portfolio Blast Radius computed at runtime:
  - Exposure: `$868,974.18` gross absolute variance.
  - Leakage: `-$588,742.42` signed net variance.
- Remediation proposal generated and revalidation executed.
- `release_decision.status` = `BLOCK_DEPLOYMENT`.

---

## TEST 3 — SYMMETRIC EQUIVALENCE (`EQUIVALENCE` → `PASS`, both directions)

1. Open **Start Assurance Mission**.
2. Select **Equivalence** mode.
3. Select Source A: `AZ_HO3_2026_09`. Select Source B: `AZ_HO3_2026_09_CLEAN`.
4. Click **Execute Assurance Mission** and confirm `release_decision.status` = `PASS`.
5. Repeat with Source A and Source B swapped (B→A) and confirm the decision is identical.

### Expected Verification:
- Both the A→B and B→A missions report `PASS` with 0 material differences.
- Source labels in the UI read neutrally as "Source A" / "Source B" (neither side is presented as authoritative).

---

## TEST 4 — INVALID INPUT REJECTION

1. Attempt to submit mission with missing product or URL `http://insecure-remote.com`.
2. Verify field-level validation prevents submission with inline error messages.

---

## TEST 5 — HISTORY ARCHIVE & PROTECTED DELETION

1. Navigate to **Assurance History**.
2. Click **Archive** on a completed audit mission → Mission status updates to `ARCHIVED`.
3. Click **Delete** on a completed audit mission → System displays safeguard modal rejecting permanent deletion of completed audit records.
4. Click **Delete** on a disposable draft/sample run → Mission is permanently deleted.

