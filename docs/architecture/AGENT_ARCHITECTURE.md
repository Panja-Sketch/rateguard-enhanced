# Gemini Structured-Decision Supervisor Architecture Specification

## 1. Overview & Vision

RateGuard AI's deployed pricing-assurance pipeline (`AssuranceSupervisor.run_mission`, `backend/app/agents/supervisor.py`) is a single deterministic Python pipeline that calls Gemini (via the **Google GenAI SDK**, configured via `RATEGUARD_GEMINI_MODEL`, defaulting to `gemini-3.7-flash`) at a fixed set of bounded decision points. This is **not** Google ADK and **not** a multi-agent framework — there is one supervisor, no inter-agent message passing, and every Gemini call returns a strict Pydantic-validated structured response (see `backend/app/agents/decision_schemas.py`) that is checked before anything downstream acts on it.

---

## 2. Fundamental Architectural Guardrail

> **Non-Negotiable Rule:** Gemini reasons, prioritizes, and explains. Deterministic Python software performs every arithmetic operation, rate table lookup, dependency graph traversal, test scenario generation and execution, reconciliation trace, and 50,000-policy portfolio exposure calculation.

Gemini's only discretion at each decision point is *which* deterministically-generated candidate (a difference, a test scenario, a remediation option) gets investigated next — every ID it returns is validated against the candidate pool before anything executes. A hallucinated ID, a malformed response, or an exhausted call budget falls back to a documented deterministic default; it never blocks the mission or silently changes the release decision.

---

## 3. Mission Pipeline & Decision Points

```text
POST /api/v1/missions
        │
        ▼
VALIDATION            deterministic — source/mode consistency
        │
        ▼
SEMANTIC_ANALYSIS      compare_packages() (AST diff)  ──Gemini──▶ PRIORITIZE_DIFFERENCES
        │
        ▼
IMPACT_ANALYSIS        dependency-graph traversal (deterministic)
        │
        ▼
RISK_DIRECTED_TESTING  candidate scenario generation ──Gemini──▶ SELECT_BOUNDARY_TESTS
                                                       ──Gemini──▶ EVIDENCE_SUFFICIENCY (bounded extra probe rounds)
        │
        ▼
RECONCILIATION         first-divergent-node trace (deterministic)
        │
        ▼
PORTFOLIO_ANALYSIS     50K-policy BigQuery scan       ──Gemini──▶ PORTFOLIO_JUSTIFICATION (waived only on zero mismatches)
        │
        ▼
REMEDIATION            Release Conformance: directional patch ──Gemini──▶ PROPOSE_REMEDIATION, SELECT_REVALIDATION_TESTS
                        Equivalence: no patch generated here    ──Gemini──▶ PROPOSE_ALIGNMENT_OPTIONS (neutral evidence only;
                                                                              directional patch is on-demand, see below)
        │
        ▼
DECISION               PASS / REVIEW_REQUIRED / BLOCK_DEPLOYMENT — deterministic, requires every signal to agree
```

### Symmetric Equivalence mode

Equivalence mode assumes neither Source A nor Source B is authoritative. `run_mission` never computes a directional patch during the mission itself — Gemini's decision at the remediation stage (`PROPOSE_ALIGNMENT_OPTIONS`) only flags which confirmed differences are material, and is evidence, not a computed fix. A concrete directional patch is generated only on demand, after a human explicitly picks a reference source, via `POST /api/v1/missions/{id}/alignment-options` (`backend/app/api/missions.py`) — itself fully deterministic (diff + remediation + revalidation), no further Gemini call.

---

## 4. Legacy pipeline (removed)

An earlier iteration of this project (`app/agents/orchestrator.py`, `runner.py`, and per-stage agent classes) explored a literal multi-agent orchestration layer with legacy `RUN-*`-prefixed run IDs. It has been removed from the codebase: nothing in the deployed product could create a new `RUN-*` job, and its docs described a system that was never the one judges actually exercised. The generic run-store read endpoints it also exposed (`GET /api/v1/assurance/runs/{id}/events` and `.../evidence`) remain — they're used by the live Mission detail page and work identically for Mission V2 `MIS-*` records.
