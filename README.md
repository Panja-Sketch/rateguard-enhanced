# RateGuard AI — Continuous Pricing Assurance for Insurance

RateGuard AI is a vendor-neutral, agentic insurance pricing assurance platform. It independently verifies that insurance pricing logic stays semantically correct as it moves from a regulatory filing, through an actuarial spec, into a rating engine implementation — catching silent pricing defects before they reach production.

**Production URL:** https://rateguard-web-iqofutwtva-uc.a.run.app

---

## The Problem

Insurance pricing logic is written once (as an approved actuarial filing) and then re-implemented multiple times: in rating engines (Guidewire, Duck Creek, Earnix, custom REST APIs), in spreadsheets, in legacy systems. A rule that is 100% correct at its source can be silently mistranslated during implementation:

- **Approved filing:** `roof_age >= 21 → factor 1.35`
- **Implemented engine:** `roof_age >= 21 → factor 1.25`

The code runs cleanly, throws no exceptions, and passes ordinary smoke tests — so incorrect customer premiums ship to production undetected. There is no standard, vendor-neutral way to prove that a rating engine implementation actually matches the filing it's supposed to implement.

## The Innovation

RateGuard converts a pricing source — a native IPIR/structured JSON spec, or a platform rating-config JSON export — into a canonical **Insurance Pricing Intermediate Representation (IPIR)**: an executable AST and dependency graph. Because every source lands in the same representation, RateGuard can compare *any* two of them symmetrically, without treating any vendor or format as the privileged source of truth. Upload a RateGuard-supported source template; the compiler validates the schema and fails closed when required pricing elements cannot be verified — RateGuard does not claim to analyze an arbitrary spreadsheet or filing PDF, only what it can genuinely and verifiably compile (see [Supported Source Formats](#supported-source-formats)).

From there, a bounded, structured Gemini supervisor and a suite of deterministic engines work together to not just detect that two sources differ, but to prove *how much it matters*: which calculation nodes are affected, which of 50,000 real policies would be mispriced, and by how much money — then propose and verify a fix.

## Agentic Gemini Workflow

RateGuard runs a mandatory deterministic evidence pipeline unconditionally (validation → IPIR comparison → dependency impact → boundary-test generation → premium oracle → target execution → trace reconciliation), and consults **Gemini 3.7 Flash** (via the Google GenAI SDK against Vertex AI) at a small, bounded set of structured decision points inside `AssuranceSupervisor`. There are seven distinct decision-point *kinds* in the code; which ones actually fire depends on what a given mission finds:

| Decision point | What Gemini decides | Fires when |
| :--- | :--- | :--- |
| `CHOOSE_EXTRACTION_STRATEGY` | Which extractor to use for a genuinely ambiguous uploaded source | Excel/PDF extraction is currently disabled in production (see [Supported Source Formats](#supported-source-formats)); this decision point exists in code but is not reachable via the live upload path today |
| `PRIORITIZE_DIFFERENCES` | Which already-detected semantic differences deserve focused boundary testing | Whenever semantic differences exist |
| `SELECT_BOUNDARY_TESTS` | Which deterministically-generated candidate boundary tests to execute | Whenever semantic differences exist |
| `EVIDENCE_SUFFICIENCY` | Whether one more bounded round of probes is worth running (capped at `MAX_PROBE_ROUNDS`) | After the first boundary-test round |
| `PORTFOLIO_JUSTIFICATION` | Whether the costly full 50K-policy scan is still warranted | Only when zero premium mismatches were reproduced despite detected differences |
| `PROPOSE_REMEDIATION` | What isolated patch would resolve a confirmed defect | Release Conformance mode, whenever a mismatch is reproduced |
| `PROPOSE_ALIGNMENT_OPTIONS` | Which confirmed differences are material to a future alignment decision — never a directional fix | Equivalence mode, whenever a difference is reproduced (neither source is presumed authoritative, so no patch is generated here — see below) |
| `SELECT_REVALIDATION_TESTS` | Which targeted + regression tests to re-run against the proposed patch | Release Conformance mode, whenever a remediation is proposed |

A judge running the standard demo path — a `RELEASE_CONFORMANCE` mission against the bundled defective target, ending in `BLOCK_DEPLOYMENT` — will see exactly **five** invocations in the Gemini Action Timeline: prioritization, boundary-test selection, evidence sufficiency, remediation proposal, and revalidation selection. Portfolio justification and extraction strategy are real code paths but conditional on the specific mission (respectively: zero reproduced mismatches, and an ambiguous uploaded source), so they won't appear in that run — this table describes what exists in the code, and the sentence above describes what one concrete production mission actually shows.

Every Gemini call is schema-validated structured output, and Gemini may **only select IDs from a candidate pool a deterministic engine already produced** — it can never invent a finding, a test scenario, or a dollar figure. Every mission is capped at `MAX_GEMINI_CALLS_PER_MISSION` calls. If Gemini is unavailable or returns an invalid response, every decision point has a deterministic fallback (e.g. "retain all differences," "use optimizer-selected tests") so a mission never stalls on an LLM outage — and the UI honestly reports which path was taken (`is_gemini_decision` / `is_fallback` on every logged action).

When two sources are found to be fully equivalent with **zero** AST diffs, Gemini is **never invoked** for that mission — the decision path never reaches a real judgment call, so there is nothing for it to decide. The UI reports this explicitly as "Gemini not invoked by design," not as a skipped or failed step.

## Architecture

```mermaid
flowchart TB
    User(["User / Judge browser"])
    Web["Next.js 14 Frontend<br/>(Cloud Run: rateguard-web)"]
    API["FastAPI API<br/>(Cloud Run: rateguard-api, public)"]
    Topic[["Pub/Sub topic<br/>assurance-runs"]]
    Worker["Worker<br/>(Cloud Run: rateguard-worker, private)"]
    Supervisor["AssuranceSupervisor<br/>(Google GenAI SDK)"]
    Gemini(("Gemini 3.7 Flash<br/>Vertex AI"))
    Engines["Deterministic Engines<br/>AST Diff · Dependency DAG · Premium Oracle<br/>Test Generator · Reconciliation · Portfolio SQL"]
    Firestore[("Firestore<br/>mission/run state")]
    BigQuery[("BigQuery<br/>50K-policy portfolio")]
    GCS[("Cloud Storage<br/>sources & evidence")]
    Decision{"Release Decision<br/>PASS / REVIEW_REQUIRED<br/>BLOCK_DEPLOYMENT"}

    User -- HTTPS --> Web
    Web -- REST --> API
    API -- "persist QUEUED" --> Firestore
    API -- publish --> Topic
    Topic -- "push, OIDC-authenticated" --> Worker
    Worker -- "acquire lease" --> Firestore
    Worker --> Supervisor
    Supervisor <-- "bounded, schema-validated calls<br/>(candidate IDs only, never raw values)" --> Gemini
    Supervisor --> Engines
    Engines --> BigQuery
    Engines --> GCS
    Supervisor -- evidence & result --> Firestore
    Supervisor --> Decision
    Firestore -. poll for status/result .-> Web

    style Gemini fill:#7c3aed,stroke:#4c1d95,color:#fff
    style Engines fill:#0369a1,stroke:#0c4a6e,color:#fff
    style Decision fill:#065f46,stroke:#022c22,color:#fff
```

The API validates a mission request synchronously (~2ms), persists it as `QUEUED` in Firestore, and publishes an `AssuranceJob` to a Pub/Sub topic. A push subscription delivers it to the private worker, which acquires an atomic Firestore execution lease (so duplicate Pub/Sub delivery can never double-execute a mission) and runs the full pipeline asynchronously — mission execution never runs in-process inside the public API. The frontend never talks to Gemini or the worker directly; it only polls the API for mission status and the final result.

## Key Features

- **Release Conformance** — verifies a target rating engine implementation conforms to an authoritative filing intent.
- **Symmetric Equivalence** — compares two sources with neither assumed authoritative: no directional patch is generated during the mission itself, and a human must explicitly pick a reference before one is computed on demand, in either direction.
- **Semantic diffing** — structural AST comparison classifying value, range, rule, order, effective-date, and rounding changes.
- **Dependency impact graph** — traces a diff through the pricing calculation DAG to every downstream affected node and output.
- **Risk-directed boundary testing** — generates targeted test scenarios at range boundaries and interaction points instead of brute-forcing the input space, and reports the real reduction (candidates pruned vs. selected) achieved.
- **Independent Premium Oracle** — a deterministic pricing engine that computes expected premiums directly from IPIR; the LLM never performs pricing arithmetic.
- **Premium reconciliation & root-cause analysis** — pinpoints the first divergent calculation node and explains why.
- **50,000-policy blast-radius analysis** — runs the confirmed defect's boundary predicate against a synthetic Arizona HO3 portfolio in BigQuery to quantify affected-policy count and net financial exposure.
- **Remediation & revalidation** — proposes an isolated patch and re-runs targeted + regression tests to prove the fix eliminates the exposure before recommending deployment.
- **Full evidence lineage** — every stage's evidence (semantic diff results, Gemini invocation metadata, reconciliation traces) is persisted to Firestore/GCS and inspectable from the mission detail UI.

## Supported Source Formats

| Format | Status |
| :--- | :--- |
| Native IPIR / structured JSON | **Supported** — deterministically compiled, strict schema validation, structured 422 errors on failure |
| Excel workbooks, PDF filings | **Not supported today.** Upload is rejected server-side (`400`) rather than silently accepted. The adapter code exists in `backend/app/adapters/` but has no verified extraction accuracy against real filings, so it is not exposed through the API — see [The Deterministic Boundary](#the-deterministic-boundary) for why RateGuard will not claim a compilation it can't stand behind. |
| YAML, CSV | **Not implemented.** No adapter exists; there is no UI path to upload one. |

RateGuard intentionally does not claim to analyze an arbitrary spreadsheet or filing PDF — only what it can genuinely and verifiably compile end-to-end. Every uploaded source is compiled through the same deterministic strict-schema path described below, and every compiled package is assigned a namespaced identity (`{ipir_package_id}--{source_id}`) so two different uploads can never collide, even if their internal `id` fields happen to match.

## Supported Source Format: JSON Schema

RateGuard compiles native IPIR JSON directly — no LLM extraction, no best-effort field guessing. The schema uses Pydantic `extra="forbid"` at every level: an unknown or misnamed top-level field (e.g. a friendly `rating_tables` instead of `tables`) is rejected with a structured `422` error naming the offending field, not silently dropped or ignored.

**Required top-level fields:**

| Field | Type | Notes |
| :--- | :--- | :--- |
| `id` | string | Unique identifier for this rate plan |
| `name` | string | Human-readable name |
| `product` | object | `{ id, name, line, jurisdiction: { country, state_or_province } }` — `line` must be one of the `InsuranceLine` enum values (`HOMEOWNERS`, `PERSONAL_AUTO`, `COMMERCIAL_AUTO`, `COMMERCIAL_PROPERTY`, `WORKERS_COMPENSATION`, `OTHER`) |
| `effective_period` | object | `{ start, end? }` (ISO dates) |
| `inputs` | array | Rating variables — each with `id`, `data_type` (`INTEGER`/`DECIMAL`/`MONEY`/`STRING`/`BOOLEAN`/`CATEGORY`/`DATE`), and range/allowed-value constraints |
| `constants` | array | Named fixed values (e.g. a base rate) |
| `tables` | array | Rate/factor lookup tables, keyed by one or more input dimensions |
| `calculations` | array | Expression nodes combining constants, table lookups, and other calculations |
| `outputs` | array | The final premium output node(s), each referencing a `calculations` node |

**Minimal working example** (`frontend/public/samples/rateguard-source-template-a.json` — also available from the Sources page as a downloadable template):

```json
{
  "ipir_version": "0.1",
  "id": "sample_rate_plan_a",
  "name": "Sample Rate Plan (Source A / Reference)",
  "version": "1.0.0",
  "product": {
    "id": "SAMPLE_HO3",
    "name": "Sample Homeowners Product",
    "line": "HOMEOWNERS",
    "jurisdiction": { "country": "US", "state_or_province": "AZ" }
  },
  "effective_period": { "start": "2026-01-01" },
  "transaction_types": ["NEW_BUSINESS", "RENEWAL"],
  "inputs": [
    { "id": "roof_age", "name": "Age of Roof", "data_type": "INTEGER", "required": true, "minimum": 0, "maximum": 100 }
  ],
  "constants": [
    { "id": "base_rate", "name": "Base Premium Rate", "value": "500.00" }
  ],
  "tables": [
    {
      "id": "roof_age_factor",
      "name": "Roof Age Factor Table",
      "dimensions": [{ "input_ref": "roof_age", "lookup_type": "RANGE" }],
      "rows": [
        { "matches": [{ "minimum": "0", "maximum": "10", "include_minimum": true, "include_maximum": true }], "value": "1.00" },
        { "matches": [{ "minimum": "11", "maximum": "20", "include_minimum": true, "include_maximum": true }], "value": "1.10" },
        { "matches": [{ "minimum": "21", "maximum": null, "include_minimum": true, "include_maximum": true }], "value": "1.35" }
      ]
    }
  ],
  "calculations": [
    {
      "id": "calculated_premium",
      "name": "Calculated Premium",
      "expression": { "operator": "MULTIPLY", "operands": [{ "ref": "base_rate" }, { "ref": "roof_age_factor" }] },
      "depends_on": ["base_rate", "roof_age_factor"]
    }
  ],
  "outputs": [
    { "id": "final_premium", "name": "Final Premium", "source_ref": "calculated_premium", "currency": "USD" }
  ]
}
```

**Expected compilation output** — on a successful `POST /api/sources/compile`, the response includes a `compilation_receipt` built directly from the compiled `IPIRPackage` (no fabricated or fallback data): `product`, `product_line`, `jurisdiction`, `effective_period_start`/`end`, and counts of `inputs`, `constants`, `tables` (plus total row count across all tables), `calculations`, and `outputs` (with their node IDs). The Sources page renders this receipt after every successful compile so you can confirm exactly what RateGuard parsed before launching a mission.

**Running a clean vs. intentional-drift comparison:** `frontend/public/samples/rateguard-source-template-b-drift.json` is identical to the template above except the `21+` roof-age factor is `1.25` instead of `1.35`. Upload the first as Source A and the second as Source B on the [Sources](https://rateguard-web-iqofutwtva-uc.a.run.app/sources) page, then launch an Equivalence mission — RateGuard reports a genuine semantic diff on `roof_age_factor` and a real premium delta ($675.00 vs. $625.00 at `roof_age=25`), not a synthetic canned result. Uploading the same file twice for both sides instead produces zero diffs and a `PASS`.

## The Deterministic Boundary

This is the architectural guarantee the whole system is built around: **Gemini reasons, plans, and explains — it never computes a premium, a financial exposure figure, or a policy count.** All arithmetic (rate table lookups, expression evaluation, rounding, DAG traversal, SQL aggregation over the 50K portfolio) is untouched deterministic Python. Gemini's only discretion is *which* deterministically-generated candidate (a difference, a test scenario, a remediation option) gets investigated next — every ID it returns is validated against the candidate pool before anything executes, and a hallucinated ID is simply rejected, falling back to the deterministic default.

## Google Cloud Services

| Service | Role |
| :--- | :--- |
| **Cloud Run** | Hosts the public API (`rateguard-api`), the private worker (`rateguard-worker`, no public ingress), and the web frontend (`rateguard-web`) |
| **Vertex AI (Gemini 3.7 Flash)** | Structured-decision reasoning via the Google GenAI SDK, authenticated via the runtime service account (no API key) |
| **Cloud Pub/Sub** | Durable async job queue between the API and worker, with a bounded retry policy and a dead-letter topic/subscription for poison messages |
| **Firestore** | Mission/run state, per-stage event log, and evidence records |
| **BigQuery** | The 50,000-synthetic-policy portfolio dataset and blast-radius exposure queries |
| **Cloud Storage (GCS)** | Uploaded source files and compiled IPIR artifacts |
| **Cloud Build** | Builds and pushes container images for the API/worker and web services |
| **Artifact Registry** | Stores built container images |

## Setup & Local Development

### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1   # Windows; use `source .venv/bin/activate` on Linux/macOS
pip install -e .
uvicorn app.main:app --reload --port 8000
```

Run tests and lint:

```bash
pytest
ruff check .
```

### Frontend

```bash
cd frontend
npm install
npm run dev      # local dev server
npm run typecheck
npm run lint
npm test
```

### Deployment

Deployment to Google Cloud Run follows a staged pipeline, implemented in `infrastructure/`:

1. `deploy_candidate.sh --deploy-candidate` builds an immutable image and deploys it as a `--no-traffic --tag candidate` revision against fully isolated staging Pub/Sub/Firestore/BigQuery/GCS resources.
2. `backend/scripts/verify_candidate.py --yes-test-candidate` exercises the candidate end-to-end (mission lifecycle, structured validation, CORS) against those isolated resources.
3. `deploy_release.sh --deploy-release --image-digest=<verified digest>` promotes the exact verified image to `--no-traffic --tag release` revisions using production resource names, still at 0% traffic.
4. Traffic is shifted deliberately via targeted `gcloud run services update-traffic` commands, gated by a staging-name guard (`infrastructure/check_production_config.sh`) that refuses to proceed if a revision resolves to any staging-named resource.

## Demo Steps

1. Open the [production site](https://rateguard-web-iqofutwtva-uc.a.run.app).
2. Visit **Missions → New Mission**, pick **Release Conformance**, and run the bundled Arizona HO3 canonical-vs-defective scenario — expect a `BLOCK_DEPLOYMENT` decision with a quantified financial exposure and a proposed remediation.
3. Run the same wizard again with the **clean control** target — expect `PASS` with zero diffs, and note the "Gemini not invoked by design" messaging.
4. Pick **Equivalence** mode and run it — note that Material Findings, Blast Radius, and every other tab use neutral "Source A" / "Source B" language throughout, never "intent" or "defective." Open the **Alignment Options** tab: no directional patch exists yet (Gemini's decision there was the neutral `PROPOSE_ALIGNMENT_OPTIONS`, not a proposed fix) — pick either Source A or Source B as the reference to generate one on demand, then pick the other to see the patch flip direction.
5. Visit **Sources**, download the two sample `.json` templates (or upload your own — see [Supported Source Format: JSON Schema](#supported-source-format-json-schema)), compile them for Source A and B, review the compilation receipt for each, and launch a mission from the real compiled sources.
6. Open the mission detail page and walk the tabs: Material Findings, Dependency DAG, Boundary Experiments, Reconciliation & RCA, Blast Radius, Remediation & Revalidation (Alignment Options in Equivalence mode), Evidence Lineage, and the Gemini Action Timeline.

## Screenshots & Video

### Demo Video

[▶ Watch the RateGuard AI Demo on YouTube](https://youtu.be/XqSU7EnHy3w)

### Release Conformance — BLOCK_DEPLOYMENT

RateGuard detects semantic pricing drift, reproduces the premium mismatch, identifies the root cause, quantifies portfolio impact, and blocks the unsafe release.

![RateGuard BLOCK_DEPLOYMENT](docs/media/rateguard-block-deployment.png)

### Production Architecture

RateGuard runs as an asynchronous Google Cloud architecture using Cloud Run, Pub/Sub, Firestore, BigQuery, Cloud Storage, and Gemini on Vertex AI.

![RateGuard Architecture](docs/media/rateguard-architecture.png)

### Reconciliation & Root Cause Analysis

The deterministic reconciliation engine identifies the first pricing node where the two implementations diverge.

![RateGuard Reconciliation](docs/media/rateguard-reconciliation.png)

### Portfolio Blast Radius

Confirmed pricing defects are evaluated against the synthetic 50,000-policy Arizona HO3 portfolio.

![RateGuard Blast Radius](docs/media/rateguard-blast-radius.png)

### Gemini Action Timeline

Gemini is used only at bounded, schema-validated decision points while deterministic engines provide the pricing evidence.

![Gemini Action Timeline](docs/media/rateguard-gemini-timeline-1.png)
![Gemini Action Timeline](docs/media/rateguard-gemini-timeline-2.png)

### Clean Control — PASS

Equivalent sources return PASS with zero semantic differences and show “Gemini not invoked by design.”

![RateGuard PASS](docs/media/rateguard-pass.png)


## Test Results

- **Backend:** 389 tests passing (`pytest`), covering mission lifecycle, validation, the Gemini supervisor's decision points and fallback paths (including the conservative-release gates below and the on-demand Equivalence-mode alignment endpoint), Pub/Sub worker delivery/idempotency, cross-process artifact storage, and API-level contract tests.
- **Frontend:** clean `tsc --noEmit` typecheck across the app.
- **Deployed acceptance tests** (`scripts/verify_deployed_system.py`, `docs/demo/DEPLOYED_ACCEPTANCE_TEST.md`): clean `RELEASE_CONFORMANCE` run → `PASS`; defective `RELEASE_CONFORMANCE` run → `BLOCK_DEPLOYMENT` with a quantified blast radius; symmetric `EQUIVALENCE` run in both directions → matching `PASS`.

## Conservative Release Decision

A pricing-assurance tool that reports a false `PASS` is worse than one that reports an honest failure. The release decision only reaches `PASS` when *every* verification signal agrees — not semantic diffing alone:

- **Behavioral evidence overrides a clean AST diff.** If the boundary-testing probes compute different premiums for Source A and Source B, that blocks the release (`BLOCK_DEPLOYMENT`) even when the semantic differ reports zero structural differences — the AST comparison catching nothing does not mean nothing changed.
- **Low-confidence extraction forces human review.** Any source compiled below `LOW_CONFIDENCE_REVIEW_THRESHOLD` never silently supports a `PASS` — the mission is downgraded to `REVIEW_REQUIRED`, even if every other signal agrees.
- **Product or jurisdiction mismatches are surfaced, not compared away.** Comparing a Homeowners source against a Personal Auto source, or two different states, isn't a meaningful equivalence check. RateGuard detects the metadata mismatch from the compiled packages and returns `REVIEW_REQUIRED` with the specific reason, instead of quietly running a comparison that was never apples-to-apples.

## Limitations

RateGuard is scoped to what it can verify end-to-end, not what would look impressive unverified:

- **Source ingestion is JSON-only today.** Excel and PDF adapter code exists (`backend/app/adapters/`) but is not exposed through the API — extraction accuracy against real filings hasn't been proven, so uploads are rejected rather than silently approximated. YAML/CSV have no adapter at all.
- **The 50,000-policy portfolio is synthetic**, generated for demo/testing purposes (`data/portfolio/`) — it is not real production policy data, and blast-radius dollar figures are illustrative of the methodology, not an actual carrier's exposure.
- **Gemini's discretion is narrow by design.** It selects among deterministically-generated candidates at a handful of fixed pipeline stages; it never performs pricing arithmetic and can't be prompted into doing so. This is a deliberate scope boundary, not a current gap — see [The Deterministic Boundary](#the-deterministic-boundary).
- **Single-tenant, single-region deployment.** No multi-tenant isolation, no authentication/authorization layer on the API beyond what Cloud Run's IAM provides at the infrastructure level — this is a hackathon-scope deployment, not a hardened multi-customer SaaS product.
- **Portfolio exposure calculations run against one synthetic Arizona HO3 dataset.** Other lines of business (auto, commercial) can compile and compare via IPIR, but the bundled 50K-policy blast-radius dataset is specific to this one product/jurisdiction; a different line's portfolio scan needs its own dataset wired in.

## License

MIT — see [LICENSE](./LICENSE).

## Repository Structure

```
backend/            FastAPI application, agents, deterministic engines, tests
  app/
    agents/          AssuranceSupervisor + Gemini decision client
    api/             REST endpoints (missions, sources, assurance, health)
    adapters/        Source format adapters (JSON, Excel, PDF, platform config)
    engines/         Deterministic diff, impact, oracle, testing, reconciliation, portfolio engines
    ipir/            IPIR schema, package model, validation
    models/           Pydantic domain models
    services/        Validation, remediation, mission transition services
    storage/         Firestore/BigQuery/GCS/Pub/Sub adapters
  scripts/            Fixture generators, demo runners, deploy-time verification scripts
  tests/              Unit, API, and agent test suites
frontend/            Next.js 14 (App Router) + TypeScript + Tailwind web UI
  src/app/            Pages (missions, sources, architecture)
  src/components/     Assurance UI components (diff viewer, impact graph, evidence lineage, ...)
  src/lib/            API client and shared types
  public/samples/     Downloadable IPIR JSON source templates (clean + intentional-drift pair)
infrastructure/      Deploy/rollback/promotion scripts and production runtime config
docs/
  architecture/       Per-subsystem architecture specifications
  demo/               Demo kit and acceptance test guide
data/                 Synthetic Arizona HO3 demo/test fixtures (rate spec, IPIR packages, 50K portfolio)
scripts/              Deployed-system verification script
```
