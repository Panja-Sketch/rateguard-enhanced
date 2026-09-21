# RateGuard Enhanced — Implementation Status

**Last updated:** 2026-09-21 (Prompt 8 — connector-backed portfolio impact, hardening and operational assurance deployed and verified; session-1 history below)

This file reports only what has been verified by running code/tests, not intent. Update it in the same change that changes the state it describes.


## Prompt 8 — connector impact, security hardening, operational assurance (2026-09-21)

Release commit `78f1857ce0f86abd170bb29ff4f779b2b58bca86` (branch `feat/prompt8-connector-impact-hardening`), deployed by digest:

| Service | Revision (100 %) | Digest | Previous (rollback) |
|---|---|---|---|
| rateguard-worker | `00004-wam` | `sha256:876617977f66fa5341b7139da8258877cd0246dc1db4bc13002789ac2cd670ee` | `00003-96v` |
| rateguard-api | `00005-xok` | same backend image | `00004-hwh` |
| rateguard-rating-engine | `00006-tjn` (same image as tagged `00004-fik`; only the demo fault variable was toggled) | `sha256:a4cf34fae98ba2955349b3afa95a9c2588d012a93573a9d564e1b1739a07f4f6` | `00003-bn5` |
| rateguard-web | `00004-zux` | `sha256:e320be78084ca929c58112bdd6234db7592fdcf5a0ae2ef99282c8c78b6523f5` | `00003-tbr` |

Live acceptance (temporary Firebase users, now disabled; synthetic data):

| Mission | Scenario | Result |
|---|---|---|
| `MIS-8DB4C882` | clean workbook vs `canonical-v1` | `PASS`; impact `COMPLETE`; 37,533 eligible compared, 0 mismatches, 12,467 out of scope (Sept. effective dates), 250 batches, 231 s |
| `MIS-BC3FDB58` | vs `defective-v1` | `BLOCK_DEPLOYMENT`; 13,446 affected, undercharge $605,070.00 (all −$45.00), 30/60/90-day renewals 953/1,277/1,365, 23 cohorts, 250 s; **identical to package-vs-package on the same 37,533 in-period policies** (13,446 / $605,070.00) |
| `MIS-71C2B921` | `canonical-v1` + 15 % injected transient faults | `REVIEW_REQUIRED`; impact `PARTIAL`, coverage 66.88 %, 4,726 inconclusive, budget exhausted; no double counting (`compared + inconclusive + out-of-scope + unprocessed = 50,000`) |
| `MIS-102F8076` | `defective-v1` + faults | `BLOCK_DEPLOYMENT`; `PARTIAL`; exposure **lower bound** $404,640.00 (62.09 % coverage) |
| `MIS-1DE6C142` | canonical, fault removed mid-scan | coverage rose to 96.92 % — batches resumed unresolved rows only; 49 batches had exhausted their 3 attempts → honest `PARTIAL`/`REVIEW_REQUIRED` |
| `MIS-210F8560` | DR drill: worker traffic rolled back to the previous revision mid-scan, then restored | stayed `BLOCK_DEPLOYMENT`; Pub/Sub 404-backoff delayed redelivery so the scan ended `PARTIAL` (228/250 batches, lower-bound exposure); results not corrupted or double counted |
| `MIS-4C39BD71` | final clean after all drills | `PASS`, `COMPLETE` |

Also verified live: Firebase login/ID-token verification on ADC (no key), RBAC (viewer 403 on export/create), cross-tenant 404 on mission/impact/bundle, unauthenticated 401, CORS allow/deny, private worker & rating engine (403 unauthenticated; API → engine via ID token), authenticated Pub/Sub push (200) incl. duplicate batch delivery for a finished job (acked, result unchanged), rate limiting (5×200 then 429), Vertex AI via worker identity (`gemini-3.1-flash-lite`, `EXPLANATION_DRAFT` succeeded after IAM cleanup), evidence bundles (hashes verified, no secret/PII strings), sampled 3,000 log lines (0 policy ids/tokens/emails), log-based metrics populated, 7 alert policies + dashboard + $25 budget exist.

Tests (final worktree): ruff clean; backend pytest ≈1,158 passed with the Firestore emulator required (`RATEGUARD_REQUIRE_EMULATOR=1`), no skips (one rate-limiter emulator test timed out once when three pytest processes shared one emulator and passed on rerun, 102/102 for `tests/ratelimit`+`tests/storage`); Firestore rules 130/130 (incl. `impact_jobs`); frontend typecheck, lint, Jest 64/64, production build, Playwright 10/10; secret scan clean.

IAM: see `docs/security/IAM_INVENTORY.md`. Firebase Admin key `1bb0ac90…` deleted, `FIREBASE_ADMIN_KEY` v1–2 and `GEMINI_API_KEY` v1 disabled.

### Remaining limitations

| Limitation | Class |
|---|---|
| Notification email channel `RateGuard operator email` is unverified until the recipient clicks Google's link | production-hardening (one manual step) |
| Default Compute SA still has `roles/editor` (Google default; Cloud Build may rely on it) | production-hardening |
| Budget alerts do not cap spend | production-hardening (by design) |
| A full 50k scan takes ~4 min on a 1-vCPU worker (CPU-bound local oracle) against the 540 s budget; scale worker CPU/instances before larger portfolios | production-hardening |
| Rolling the worker back mid-scan degrades that scan to `PARTIAL` (Pub/Sub 404 backoff up to 10 min); retry the mission | production-hardening |
| For a `PARTIAL` scan, `eligible`/out-of-scope only count processed rows | optional enhancement |
| Sources page does not render `compatibility.state` (REUPLOAD_REQUIRED is enforced server-side and shown in the mission decision) | optional enhancement |
| Live Gemini fallback and Firestore-outage rate-limit alerts were not induced | optional enhancement |
| Single-quote-only connectors need ≈ rows/QPS seconds and may end `PARTIAL` | documented behaviour |
| Synthetic portfolio; 12,467 policies (Sept. dates) fall outside the workbook's effective period and are disclosed, not priced | documented behaviour |

No release blockers.

## Session 1 — Shared foundations (IPIR v0.2 + rating-engine foundation)

| Checkpoint | Description | Status |
|---|---|---|
| Planning | `IMPLEMENTATION_PLAN.md`, `STATUS.md`, `DECISIONS.md` authored and internally consistent with the locked source of truth | DONE |
| CP0 | IPIR v0.2 contract module (`backend/app/ipir/v0_2/`) | DONE — verified by 53 passing tests in `tests/ipir/v0_2/` |
| CP1 | v0.1 recursive `extra=forbid` hardening | DONE — verified by `tests/ipir/test_extra_forbid.py` (20 leaf-model cases) and a full regression run of the pre-existing 398-test suite (unchanged, all pass) |
| CP2 | Semantic validation (duplicate IDs, refs, cycles, ranges, currency/scale/rounding, zero-division, compatibility gate) | DONE — see per-item verification below |
| CP2a | `MissionStage` 20-stage enum + stage-outcome model (foundation only, not wired into supervisor) | DONE — verified by `tests/models/test_stages.py`; **not** wired into `agents/supervisor.py` (deferred to CP9, per D4) |
| CP3 | v0.2 → v0.1 compatibility/lowering boundary (`app.ipir.v0_2.compat`) | DONE — verified by `tests/ipir/v0_2/test_compat_lowering.py`, including the golden fixtures and every expression kind |
| CP4 | Deterministic golden IPIR v0.2 fixtures ($700 / $655) | DONE — `data/implementations/v0_2/{canonical,defective}/AZ_HO3_GOLDEN_ipir.json`, generated by `backend/scripts/generate_ipir_v0_2_golden_fixtures.py`, both control cases pass exactly |
| CP5 | `backend/rating_engine` separate service foundation | DONE — own `main.py`/`Dockerfile`/startup self-test; verified via `TestClient` (no real network) in `tests/rating_engine/` |
| CP6 | Unit/regression tests, full suite run | DONE — see test results below |

### CP2 item-by-item verification

- **Duplicate IDs** — `test_rejects_duplicate_ids_across_entity_types` (cross-entity-type collision caught).
- **Unresolved references** — `test_rejects_unresolved_reference_in_calculation`, `test_rejects_unresolved_table_reference`, `test_rejects_dangling_output_source_ref` (walks the full expression tree, not just top-level operands).
- **Calculation cycles** — `test_rejects_calculation_cycle` (declared `depends_on`) and `test_rejects_cycle_hidden_purely_in_expression_reference_not_declared_depends_on` (cycle visible only via expression references, proving the dependency graph is derived, not just declared).
- **Range gaps / ambiguous overlaps** — `tests/ipir/v0_2/test_table_validation.py` (8 cases: non-overlapping, ambiguous overlap without priority, resolved overlap with distinct priority, overlap with shared priority still flagged, gap with/without a declared default, full coverage via touching boundaries, EXACT-lookup tables correctly skipped).
- **Effective periods** — `test_effective_period_end_before_start_rejected` (reuses v0.1's existing `EffectivePeriod` validator).
- **Explicit currency/scale/rounding** — `test_output_requires_currency_scale_and_rounding_mode` (structural: Pydantic enforces presence, no defaults) plus `test_output_scale_mismatch_with_source_round_expression_rejected` (cross-checks the output's declared contract against its source calculation's actual rounding).
- **Zero-division** — `test_binary_expression_divide_rejects_unsupported_zero_behavior` (model-level) and `test_divide_by_zero_still_rejected_after_lowering` (still rejected end-to-end through the lowered v0.1 evaluator).
- **Package compatibility** — `tests/ipir/v0_2/test_compatibility.py` (7 cases: identical packages compatible, product line/jurisdiction/currency/effective-period/transaction-type mismatches each individually reported, schema major-version match, and confirmation that a mismatch is always reported as data — never a bare boolean silently collapsed to a pass).

### Rating-engine golden-value proof

`backend/rating_engine/startup_selftest.py::run_startup_selftest()` executes the exact golden case (`roof_age=25`) against both engines through the real `/quote`-serving code path (via `TestClient`, no real network):

```
canonical-v1 -> 700.00
defective-v1 -> 655.00
```

Both match locked doc section 8.3 exactly.

## Deferred (not part of this session)

CP7 Controlled Workbook v1 compiler (includes executing D1's PDF/PlatformConfig/extraction-strategy removal) · CP8 Connector registry/REST client/SSRF · CP9 Supervisor wiring to `MissionStage` · CP10 Frontend · CP11 Deployment · CP12 Full test-pyramid backfill.

## Test results (this session)

Command: `cd backend && python -m pytest -q`

- **Before any change (baseline):** 398 passed, 0 failed.
- **After CP0–CP6 (final run):** 489 passed, 0 failed, 218 warnings (all pre-existing Pydantic v1-style `.dict()` deprecation warnings and one Starlette `HTTP_422_UNPROCESSABLE_ENTITY` rename notice — both pre-existing in the codebase, untouched by this session, and functionally harmless). Runtime 852s.
- New tests added this session: 91, confirmed by `pytest --collect-only` — `tests/ipir/v0_2/` (53: 15 expressions, 15 package validation, 8 table validation, 8 compatibility, 12 compat/lowering — arithmetic per-file may not sum exactly due to shared parametrized cases; 53 is the collected total for the directory), `tests/ipir/test_extra_forbid.py` (20, parametrized), `tests/models/test_stages.py` (6), `tests/rating_engine/` (18: 2 startup self-test + 8 quote-API — total 91 matches 489 − 398 exactly).
- No test removed, skipped, or modified from the pre-existing suite.

## Known current-state facts (carried forward, still accurate)

- Excel and PDF source adapters still fabricate results (ignore uploaded content) — untouched this session. D1's removal remains open (see DECISIONS.md D5's last bullet); it was judged out of scope for CP7 specifically because the session-2 task list scoped work entirely to `backend/app/ingestion/workbook_v1/` and did not ask for supervisor/adapter/API changes.
- No REST rating-engine *connector* (client/SSRF/registry) exists yet — only the target-side service (`backend/rating_engine`) built in session 1. Nothing in `backend/app` calls it yet.
- `MissionStage` enum exists (`backend/app/models/stages.py`) but `agents/supervisor.py` does not yet use it — the 8-internal-stage model is still what actually runs missions today.
- IPIR v0.1 (`backend/app/ipir/*.py`, excluding `v0_2/`) is unchanged in behavior; only additive schema hardening (`extra=forbid`, optional `TableRow.priority`/`RateTable.requires_total_coverage`/`RateTable.default_value` fields) was added. Existing v0.1 JSON documents without these fields parse identically to before.
- The new `backend/app/ingestion/workbook_v1/` compiler is standalone: nothing in `backend/app/api/*`, `backend/app/agents/*`, or the mission pipeline calls it yet. Wiring a real upload/compile API endpoint to it is not part of this session's scope and remains open for CP8/CP9-adjacent work.

## Session 2 — Controlled Workbook v1 compiler (CP7)

**Scope:** Built `backend/app/ingestion/workbook_v1/`, the Controlled RateGuard Workbook v1 compiler (locked doc section 5), entirely on top of session 1's IPIR v0.2 models/validators/lowering boundary — no session-1 file was modified.

### Files added

- `backend/app/ingestion/__init__.py`, `backend/app/ingestion/workbook_v1/{__init__,limits,errors,zip_safety,sheets,formulas,mapping,receipt,sanitize,compiler}.py`
- `backend/scripts/generate_workbook_v1_samples.py`
- `data/samples/workbook_v1/canonical/AZ_HO3_GOLDEN_workbook.xlsx`, `data/samples/workbook_v1/defective/AZ_HO3_GOLDEN_workbook.xlsx`, `data/samples/workbook_v1/negative/*.xlsx` (21 fixtures) — all generated, none hand-edited
- `backend/tests/ingestion/__init__.py`, `backend/tests/ingestion/workbook_v1/{__init__,conftest,test_compiler,test_sanitize}.py`

### What was verified by running code

- **Canonical workbook** (`data/samples/workbook_v1/canonical/AZ_HO3_GOLDEN_workbook.xlsx`) compiles to `status="VERIFIED"`, embedded control case `golden_case` passes with `actual="700.00"` exactly. Verified by `test_canonical_workbook_compiles_and_produces_700`.
- **Defective workbook** compiles to `status="VERIFIED"` with its own control case proving `actual="655.00"` (see the A2-scoping note below and in DECISIONS.md D5 for exactly what this test does and does not claim). Verified by `test_defective_workbook_compiles_and_control_case_proves_655`.
- **21 distinct negative fixtures**, each produced programmatically by `generate_workbook_v1_samples.py` (ZIP/entry manipulation of a real base workbook, or plain openpyxl cell writes — never hand-edited binaries), each independently confirmed via `compile_workbook` to raise the intended, distinct error code. Full list and locked-doc mapping below.
- **Sanitizer** (`sanitize.py`) unit-tested directly (8 tests) and end-to-end through the compiler boundary (1 test): an injected `RG_METADATA` value containing both a formula-injection payload (`=cmd|'/c calc'!A1`) and HTML markup pattern comes out with no leading raw `=` and with markup-significant characters HTML-escaped.
- **Grep self-audit**: no `eval(`, `exec(`, `subprocess`, `os.system`, `pickle.load`, or `yaml.load` anywhere in `backend/app/ingestion/workbook_v1/` (confirmed by direct grep, not by inspection alone).
- **Full existing suite regression**: `cd backend && python -m pytest -q` — before this session's changes: 489 passed (session 1's final count, itself 398 pre-existing + 91 from session 1). After this session's changes: see the run recorded immediately below; CP7 is purely additive (no existing file was modified), so no regression was expected or found.

### Locked doc section 17.1 / acceptance-scenario coverage

| Locked item | Test function |
|---|---|
| Valid canonical workbook compiles, $700.00 | `test_canonical_workbook_compiles_and_produces_700` |
| Valid defective workbook compiles/control case proves $655.00 | `test_defective_workbook_compiles_and_control_case_proves_655` (narrowed scope — see DECISIONS.md D5) |
| Unknown function rejects (A8) | `test_unknown_function_rejects_matching_a8` |
| Macro-enabled workbook rejects | `test_macro_enabled_workbook_rejects`, `test_macro_enabled_via_xlsm_extension_rejects` |
| External link rejects | `test_external_link_rejects` |
| Hidden dependency outside contract rejects | `test_hidden_dependency_outside_contract_rejects` |
| Duplicate IDs reject | `test_duplicate_ids_reject` |
| Missing control case causes REVIEW_REQUIRED | `test_missing_control_case_causes_review_required` |
| Overlapping ambiguous ranges reject | `test_overlapping_ambiguous_ranges_reject` |
| Formula cycle rejects | `test_formula_cycle_rejects` |
| Tampered workbook changes source hash / invalidates prior attestation | `test_tampered_workbook_changes_source_hash_and_invalidates_attestation` |

Additional session-instructed negative coverage beyond the locked bullet list, all passing: OLE/embedded object (`test_ole_embedded_object_rejects`), password-protected/encrypted (`test_password_protected_encrypted_workbook_rejects`), oversized file (`test_oversized_file_rejects`), ZIP bomb/compression ratio (`test_zip_bomb_compression_ratio_rejects`), excessive ZIP entries (`test_excessive_zip_entry_count_rejects`), path traversal (`test_path_traversal_entry_name_rejects`), non-.xlsx extension/signature (`test_non_xlsx_extension_rejects`, `test_bad_file_signature_rejects`), missing required sheet/column (`test_missing_required_sheet_rejects`, `test_missing_required_column_rejects`), division-by-zero path (`test_division_by_zero_path_rejects`), missing rounding on an output (`test_missing_rounding_on_output_rejects`), currency inconsistency (`test_currency_inconsistency_across_outputs_rejects`), empty file (`test_empty_file_rejects`), and a "never raises" contract test (`test_never_raises_always_returns_a_receipt`).

## Test results (session 2)

- New tests added in `backend/tests/ingestion/workbook_v1/`: 35, all passing (27 in `test_compiler.py`, 8 in `test_sanitize.py`).
- Full-suite run before session 2's changes: 489 passed, 0 failed (session 1's final count).
- Full-suite run after CP7's core module (before the D6 ingestion-boundary wiring): 524 passed, 0 failed, 761.81s — confirmed independently twice (once by the implementing agent, once by an independent re-run), exactly matching 489 + 35.
- Full-suite run after D6's ingestion-boundary wiring (`ingestion_service.py` changes, 2 net-new tests replacing/added in `tests/agents/test_extraction_orchestration.py`): **526 passed, 0 failed**, 741.39s. Command: `cd backend && python -m pytest -q`.

### Ingestion-boundary wiring (D6, after CP7's core module was built)

Review of the D1 concern found the real user-reachable upload path, `PricingSourceIngestionService.register_source`, already rejected `.xlsx`/`.xls`/`.pdf` before any `SourceFormat.EXCEL` `SourceDescriptor` could exist — so the fabricating `ExcelPricingAdapter`/Gemini-extraction-strategy path was already unreachable from the real API, only exercised by direct internal unit tests. Given the user's explicit choice ("minimal fail-closed fix now" over full D1 execution — see DECISIONS.md D6), `.xlsx` is now routed at that same boundary to the real compiler instead of being hard-rejected:

- `register_source` accepts `.xlsx` (still not legacy `.xls`) as `SourceFormat.EXCEL`.
- `compile_source` calls a new `_compile_controlled_workbook` helper (`app/services/ingestion_service.py`) for `SourceFormat.EXCEL` sources — calls `compile_workbook` directly, lowers a non-`REJECTED` result to v0.1 via `lower_to_v0_1`, and raises `SourceParsingError` on `REJECTED`. `agents/supervisor.py` and the legacy Excel adapter/Gemini extraction-strategy code were **not modified** and are confirmed-by-test unreachable for `.xlsx` specifically; PDF still uses that unchanged path (out of scope, per locked doc section 4.2).
- Verified by `tests/agents/test_extraction_orchestration.py::test_ingestion_service_rejects_legacy_xls_and_pdf_uploads` (narrowed from the prior test, whose `.xlsx`-must-reject assertion is now the opposite of intended behavior), `test_ingestion_service_rejects_unsafe_xlsx_workbook`, and `test_ingestion_service_compiles_canonical_workbook_v1_sample_end_to_end` (real `register_source` → `compile_source` round trip on the generated canonical sample reaches `VERIFIED`/confidence `1.0`).

### Known limitations / judgment calls a reviewer should sanity-check

- **Full D1 not executed.** PDF and `.xls` ingestion still resolve through the legacy `agents/supervisor.py` Gemini-extraction-strategy path (`.xls`/`.pdf` are hard-rejected before reaching it, PDF is not); `PLATFORM_CONFIG` ingestion and the `CHOOSE_EXTRACTION_STRATEGY` Gemini decision remain in the codebase for that format. Removing them fully is unresolved follow-up work — see DECISIONS.md D6.
- **Gemini assisted-mapping (locked doc section 5.4) is not implemented, by design** — explicitly out of scope for this pass. No stub, no fake confidence score, no autonomous retry loop was built in its place.
- **Encrypted-workbook fixture is a signature-only OLE/CFB stub**, not a fully-formed encrypted OOXML container (openpyxl cannot produce one). See DECISIONS.md D5 for why this still exercises the real code path rather than simulating it.
- **Compression-ratio/entry-count constants** (`MAX_ZIP_ENTRIES=2000`, `MAX_TOTAL_UNCOMPRESSED_BYTES=200 MiB`, `MAX_ENTRY_COMPRESSION_RATIO=100x`) are conservative, documented judgment calls, not derived from a benchmark corpus of real actuarial workbooks (none exists yet for this contract).
- **A2-scoping decision**: the "defective workbook" test is scoped strictly to what a workbook *compiler* can honestly claim (internal self-consistency against its own embedded control case) and explicitly does not perform any canonical-vs-defective cross-engine comparison, which belongs to the mission-level reconciliation engine, not this module.
- **The RG_CALCULATIONS mini-DSL's IF condition grammar is deliberately narrow** (one comparison, or exactly two joined by a single AND/OR) — matching the locked doc's "limited IF" language, not a general boolean-expression parser.
- **Upload endpoint / GCS quarantine storage / mission-pipeline wiring beyond `PricingSourceIngestionService`** (e.g. an actual `POST /sources/uploads` HTTP route, mission `SOURCE_A_LOAD`/`SOURCE_B_LOAD` stage consumption) is unchanged and out of scope for this session — `compile_workbook` and now `PricingSourceIngestionService` are real and tested, but the broader mission pipeline does not yet drive them.

## Session 3 — REST rating-engine connector (CP8)

**Scope:** Built `backend/app/connectors/`, the client side of the locked doc section 8 REST target-engine connector (registry, versioned request/response contract, real HTTP client with SSRF/timeout/retry controls, golden-case health-test function), calling the real `backend/rating_engine` demo service (session 1) over real HTTP semantics via `httpx`. `backend/rating_engine/*` was not modified. CP7's uncommitted files (`backend/app/ingestion/*`, `backend/app/services/ingestion_service.py`, `backend/tests/agents/test_extraction_orchestration.py`, `backend/scripts/generate_workbook_v1_samples.py`, `backend/tests/ingestion/*`, `data/samples/*`) were not touched — confirmed by `git status` showing the identical modification set before and after this session's changes.

### Files added

- `backend/app/connectors/{__init__,errors,contract,redact,retry,budget,security,registry,client,health}.py`
- `backend/tests/connectors/{__init__,conftest,test_registry,test_contract,test_security,test_retry,test_redaction,test_client_golden,test_client_negative,test_health}.py`

### Files modified

- `backend/app/core/config.py` — added four new `Settings` fields (`rating_engine_connector_base_url`, `rating_engine_connector_is_local_dev`, `rating_engine_connector_auth_header_name`, `rating_engine_connector_auth_token_env_var`), additive only, all with safe defaults (`is_local_dev=True`, base URL pointing at the local demo service, no auth configured by default). No existing field changed.

### What was verified by running code

- **Baseline before this session's changes:** `cd backend && python -m pytest -q` → **526 passed, 0 failed**, 892.82s (matches session 2's final count exactly — no drift).
- **New `backend/tests/connectors/` suite run in isolation:** **58 passed, 0 failed**, 0.15s (all real assertions; the `test_pydantic_dict_str_str_actually_rejects_a_float_in_this_config` test's expectation was corrected after actually running it once and observing the real behavior of the pinned Pydantic version, per the task's explicit "verify actual behavior with a real test rather than assuming" instruction — see D7). No `xfail`, no `skip`, confirmed by grep.
- **Full suite after this session's changes:** see the run recorded immediately below.
- **Registry fail-closed behavior:** `test_select_connector_fails_closed_for_unregistered_connector`, `test_select_connector_fails_closed_for_undeclared_engine_version`, `test_select_connector_never_falls_back_to_a_default` — an unregistered `connector_id` or undeclared `engine_version` always raises a typed exception carrying `ConnectorFailureCategory.NON_RETRYABLE`, never silently resolves to the one real demo entry.
- **Golden values reproduced through the full client path** (registry selection → HTTPS/SSRF checks → real HTTP request over `httpx.ASGITransport` wrapping the real `rating_engine.main.app` → schema validation): `canonical-v1` → `$700.00`, `defective-v1` → `$655.00` (`test_client_golden.py`).
- **SSRF/destination-safety checks exercise real `ipaddress`/`socket.getaddrinfo` logic**, not mocks: private RFC1918 addresses, loopback without the local-dev flag, the AWS/GCP-style `169.254.169.254`/`fd00:ec2::254` metadata addresses, and a public IP literal are each asserted against the real stdlib classification (`test_security.py`). No test performs a real DNS query — every host used is either an IP literal (no resolver invoked) or `localhost`/a loopback address resolved via the OS-local mechanism.
- **Log redaction proven with a real captured log record**, not just the `scrub_secrets` unit function in isolation: `test_secret_never_appears_unredacted_in_captured_log_output` drives a real client failure path (an injected transport raising a connection error whose message embeds a bearer-token-shaped secret) through `caplog` and asserts the secret string is absent from every captured record.
- **Backoff/jitter is a pure function**, unit-tested for bounds (zero at `random_fn()==0`, exact ceiling at `random_fn()==1`, capped growth, monotonic increase across attempts 1–5) with no real sleeping anywhere in `test_retry.py`.
- **Retry/backoff integration**: a fake target failing with 429 twice then succeeding is retried transparently (`test_429_retries_then_succeeds_with_backoff_mocked`); a fake target failing with 503 forever is retried exactly to the locked doc section 16.2 cap of five attempts and then raises `CONNECTOR_UPSTREAM_ERROR`/`RETRYABLE` (`test_5xx_retries_capped_at_five_attempts`); a fake target returning 400 is never retried at all (`test_4xx_application_rejection_is_never_retried`) — all with an injected no-op sleep so the suite stays fast (0.15s total).
- **Grep self-audit**: `grep -rln "app.connectors" app/ tests/` outside `app/connectors/`/`tests/connectors/` found only a documentation comment in `app/core/config.py` (pointing a reader at the registry module) — no actual import. Nothing in `app/agents/supervisor.py`, `app/api/*`, or `app/missions/*` calls the new connector module.

### Locked doc section 17.1 / 16.2 / 8.2 coverage table

| Locked item | Test function |
|---|---|
| Canonical result ($700.00) | `test_client_golden.py::test_canonical_result_is_700` |
| Defective result ($655.00) | `test_client_golden.py::test_defective_result_is_655` |
| Authentication denied | `test_client_negative.py::test_authentication_denied` (and the positive counterpart `test_authentication_succeeds_with_correct_token`) |
| Timeout | `test_client_negative.py::test_timeout_triggers_and_is_classified_retryable` (deterministic transport-level timeout injection — see D7/inline docstring for why `httpx.ASGITransport` does not itself enforce `httpx.Timeout`) |
| 429 and 5xx retry, capped at five | `test_client_negative.py::test_429_retries_then_succeeds_with_backoff_mocked`, `test_5xx_retries_capped_at_five_attempts` |
| 4xx never retried | `test_client_negative.py::test_4xx_application_rejection_is_never_retried` |
| Malformed JSON | `test_client_negative.py::test_malformed_json_rejected` |
| Wrong request ID | `test_client_negative.py::test_wrong_request_id_rejected` |
| Wrong engine_version (additional) | `test_client_negative.py::test_wrong_engine_version_rejected` |
| Partial batch (incomplete outputs) | `test_client_negative.py::test_partial_batch_incomplete_outputs_rejected` |
| Oversized response | `test_client_negative.py::test_oversized_response_rejected` |
| Redirect denied | `test_client_negative.py::test_redirect_rejected_not_followed` |
| SSRF: private/loopback/link-local/metadata address rejected | `test_security.py::test_private_rfc1918_address_rejected`, `test_cloud_metadata_address_rejected`, `test_ipv6_metadata_style_link_local_rejected`, `test_loopback_ip_literal_rejected_without_local_dev_flag`, plus the integration-level `test_client_rejects_private_destination_before_connecting` |
| Duplicate request returns stable response | `test_client_golden.py::test_duplicate_request_id_returns_stable_response` (see D7 for the honest "stable" scoping) |
| Float-typed output rejected (task-specific) | `test_client_negative.py::test_float_output_rejected_explicitly_not_relying_on_pydantic_coercion` + `test_pydantic_dict_str_str_actually_rejects_a_float_in_this_config` |
| Unsupported trace node rejected (task-specific) | `test_client_negative.py::test_unsupported_trace_node_rejected`, `test_contract.py::test_trace_step_rejects_unsupported_node_type`/`test_trace_step_rejects_unsupported_operation` |
| Log redaction | `test_redaction.py::test_secret_never_appears_unredacted_in_captured_log_output` |
| Backoff/jitter pure-function bounds | `test_retry.py` (6 cases) |
| §8.2 HTTPS-outside-local-dev enforcement | `test_security.py::test_http_rejected_outside_local_dev`, `test_http_rejected_even_in_local_dev_for_non_loopback_host`, `test_http_allowed_for_loopback_in_local_dev` |
| §13.2 safe metadata only, no credentials | `test_registry.py::test_metadata_never_exposes_base_url_or_credentials` |
| §13.2 golden-case health test | `test_health.py::test_health_check_passes_for_real_canonical_engine`, `test_health_check_fails_closed_for_unregistered_connector` |
| extra="forbid" contract enforcement | `test_contract.py::test_request_forbids_unknown_fields`, `test_response_forbids_unknown_fields` |

### Known limitations / judgment calls a reviewer should sanity-check

- **DNS-rebinding is mitigated, not eliminated** — see D7 for the honest, specific residual-limitation statement (the transport connects by hostname, not by the pre-validated IP object).
- **The real-timing timeout path** (a target genuinely slower than the 3s/10s locked timeouts, observed end-to-end over a real socket) was verified manually during development against a real bound TCP server, but is not re-asserted as an automated test — `httpx.ASGITransport` was found (by actually running the test, not by assumption) not to enforce `httpx.Timeout` for a purely in-process ASGI call, so the automated suite instead deterministically tests the connector's own catch-and-classify logic for a real `httpx.TimeoutException` via a transport that raises one directly. Documented rather than silently worked around.
- **No live FastAPI route added** for `POST /connectors/{connector_id}/test` — `app/connectors/health.py` provides the function; wiring an authenticated admin route under `app/api/*` is left to the future integration session, per the task's own explicit scoping.
- **Auth-header mechanism is untested against the real demo target** because that target enforces no authentication today (by design, per D3) — it is proven end-to-end only against a purpose-built fake target in `tests/connectors/conftest.py::make_auth_required_app`.
- **Idempotency claim is intentionally weak and documented as such** ("same inputs + same request_id → same outputs" for a stateless deterministic target), not a request-id-keyed response cache — see D7.
- **Response-size cap (1 MiB) and the two locked exact timeout numbers (3s/10s) are the only "chosen constant" judgment calls in this module** — the size cap is a documented, conservative pick (see D7); the timeouts are copied verbatim from the locked doc, not chosen.

## Session 4 — CP9: wiring the Controlled Workbook v1 compiler + REST connector into mission orchestration

**Scope:** Wired the previously-standalone CP7 (Controlled Workbook v1 compiler) and CP8 (REST connector client) modules into the real `AssuranceSupervisor.run_mission`/`MissionExecutionService.execute_job` mission pipeline — no second mission engine — plus the minimum API/frontend surface to drive it end to end, plus the deferred CP9 stage-recorder wiring (all 20 `MissionStage` values). See `docs/implementation/DECISIONS.md` (D8) for every implementation-shape judgment call.

### Files added

- `backend/app/api/connectors.py` (`GET /api/v1/connectors`)
- `backend/tests/agents/test_supervisor_connector_path.py`, `backend/tests/unit/test_connectors_api.py`, `backend/tests/unit/test_stage_recorder_wiring.py`, `backend/tests/unit/test_sources_api.py`, `backend/tests/integration/__init__.py`, `backend/tests/integration/test_workbook_to_connector_mission_e2e.py`

### Files modified

- `backend/app/models/mission.py` — new `ConnectorSelection` model; additive `connector_id`/`engine_version` fields on `PricingSourceRef`.
- `backend/app/models/result_v2.py` — additive `stage_outcomes: list[StageOutcome]` field on `AssuranceResultV2`.
- `backend/app/models/__init__.py` — exports `ConnectorSelection`.
- `backend/app/storage/models.py` — new `EvidenceType.CONNECTOR_INVOCATION`.
- `backend/app/services/validation_service.py` — connector-selection validation (`select_connector`) for `API_CONNECTOR` sources, fail-closed at mission-create time.
- `backend/app/services/mission_execution_service.py` — `_resolve_source_package` returns `None` for `API_CONNECTOR`; `execute_job` builds `ConnectorSelection` and passes `target_connector=` into `run_mission`.
- `backend/app/agents/supervisor.py` — `run_mission` gains `target_connector` parameter; new `_quote_via_connector`/`_run_probe` connector branch (real `ConnectorClient.send_quote`, `asyncio.run` bridge, per-probe `CONNECTOR_INVOCATION` evidence, hard-failure/partial-response guards); connector-path reconciliation branch; effective-period compatibility check (JSON-vs-JSON path); full `StageRecorder`/`MissionStage` wiring across every code path (clean-equivalence fast path, semantic-diff-blind-spot fast path, full material-drift path); injectable `connector_client_factory` constructor parameter for testability.
- `backend/app/api/sources.py` — `compile_pricing_source` now returns `workbook_compilation_receipt` (the real CP7 `CompilationReceipt`) alongside the pre-existing generic receipt.
- `backend/app/api/missions.py` — new `GET /missions/{id}/connector-evidence` endpoint (fixed-field whitelist, mirrors the existing Gemini-evidence endpoint's restraint).
- `backend/app/main.py` — registers the new connectors router.
- `backend/tests/agents/test_worker_delivery_outcomes.py` — updated fake `run_mission` signatures for the new `target_connector` parameter; new `test_no_duplicate_execution_under_redelivery_for_connector_backed_mission`.
- `backend/tests/unit/test_missions_v2.py` — new connector-selection validation test cases.
- `frontend/src/lib/api/client.ts` — `WorkbookCompilationReceipt`, `ConnectorMetadata`, `listConnectors`, `getConnectorEvidence`, `ConnectorInvocationEvidence` types/functions; `compileSource`'s return type includes `workbook_compilation_receipt`.
- `frontend/src/lib/types/assurance.ts` — `MissionStageOutcome` type; `AssuranceResultV2.stage_outcomes`.
- `frontend/src/app/sources/page.tsx` — `.xlsx` accepted in the Source A/B file inputs; a "RateGuard Controlled Workbook v1" supported-format card with explicit unsupported-format callouts; workbook compilation receipt rendering; a Source B "Upload File" / "Live Connector" toggle with a connector + engine-version picker (dropdown only, never a free-text URL) wired into mission creation.
- `frontend/src/app/missions/[missionId]/page.tsx` — new "Stage Ledger" tab rendering the full 20-stage `stage_outcomes` list (status + reason per stage) and a connector-invocation-evidence panel (hashes/status only, fetched from the new endpoint).

### What was verified by running code

- **Locked golden case proven end-to-end through the real supervisor, real `ConnectorClient`, and real `backend/rating_engine` service** (via `httpx.ASGITransport`, no real network): canonical-v1 → $700.00 (0 mismatches, `PASS`); defective-v1 → $655.00 vs expected $700.00 (`BLOCK_DEPLOYMENT`, exact first-divergent-node and root-cause populated). `tests/agents/test_supervisor_connector_path.py` (4 tests, direct supervisor call) and `tests/integration/test_workbook_to_connector_mission_e2e.py` (3 tests, real `POST /sources` → `/compile` → `POST /missions` → real Pub/Sub push endpoint → `GET /missions/{id}`).
- **Connector hard-failure (all probes fail) and partial-response (REVIEW_REQUIRED-category failure) never produce `PASS`** — proven by dedicated fake-client tests in `test_supervisor_connector_path.py`.
- **Duplicate Pub/Sub delivery for a connector-backed mission still dedups correctly** — `test_no_duplicate_execution_under_redelivery_for_connector_backed_mission` confirms the supervisor (and therefore the connector call inside it) is invoked exactly once across two deliveries of the same job.
- **Every one of the 20 locked `MissionStage` values is recorded on every real mission path exercised in tests** (clean equivalence, material-drift Release Conformance, connector-backed Release Conformance) — `test_stage_recorder_wiring.py` and the connector-path tests assert `set(stage_outcomes) == set(MISSION_STAGE_ORDER)`, and that the four genuinely-unbuilt stages (`COHORT_DISTRIBUTION`, `PIPELINE_IMPACT`, `EXPLANATION_FACTS`, `EXPLANATION_DRAFT`) are always `NOT_APPLICABLE` with an honest reason.
- **Workbook compilation receipt is now actually returned over HTTP** — `test_sources_api.py::test_canonical_workbook_upload_surfaces_real_compilation_receipt` asserts `workbook_compilation_receipt.status == "VERIFIED"`, `compiler_version`, `artifact_sha256`, and a passing `700.00` control-case result are all present in the real `POST /sources/{id}/compile` response; a negative-fixture workbook upload returns 400 with no stack trace.
- **`GET /api/v1/connectors` never leaks a base URL or credential** — `test_connectors_api.py` asserts on the raw response text.
- **Full existing backend suite regression**: two pre-existing tests in `test_worker_delivery_outcomes.py` initially failed after `run_mission`'s signature changed (fake `run_mission` doubles didn't accept the new `target_connector` kwarg) — fixed by updating the fake signatures; confirmed passing afterward. Full-suite pass/fail counts below.
- **Frontend TypeScript**: `npm run typecheck` (`tsc --noEmit`) passes with zero errors after all `sources/page.tsx`, `missions/[missionId]/page.tsx`, `lib/api/client.ts`, and `lib/types/assurance.ts` changes.

### Test results (session 4)

Command: `cd backend && python -m pytest -q`

- New tests added this session: `test_supervisor_connector_path.py` (4), `test_connectors_api.py` (1), `test_stage_recorder_wiring.py` (2), `test_sources_api.py` (2), `test_workbook_to_connector_mission_e2e.py` (3), `test_missions_v2.py` connector-validation additions (4), `test_worker_delivery_outcomes.py` connector-duplicate-delivery addition (1) — 17 net-new test functions, all independently run and passing (per-file runs recorded during implementation).
- Full-suite run before this session's changes: 582 passed (matches session 3's final count).
- Two pre-existing tests in `test_worker_delivery_outcomes.py` (`test_cancelled_mission_acks`, `test_no_duplicate_execution_under_redelivery`) initially failed immediately after `run_mission`'s signature changed (their fake `run_mission` doubles did not accept the new `target_connector` keyword argument) — fixed in the same session by updating the fake signatures; the full `test_worker_delivery_outcomes.py` file (29 tests) was re-run and confirmed passing.
- Full-suite run after this session's changes: **598 passed, 0 failed**, 1002.48s (0:16:42). Command: `cd backend && python -m pytest -q`. (582 baseline + 16 net-new test functions actually collected this session, all passing; the 2 initially-regressed tests were fixed before this run and are counted among the 598.)
- Frontend: `npm run typecheck` (`tsc --noEmit`) — 0 errors. `npm run build`/Playwright E2E — see "Known limitations" below; no Playwright infrastructure exists in this repository, and none was added this session.

### Known limitations / judgment calls a reviewer should sanity-check

- **Fully automatic connector-path test generation does not always reproduce the exact locked $700.00/$655.00 figures for an arbitrary workbook** whose numeric inputs have an open-ended declared range (the shipped golden workbook's `roof_age` has `minimum=0`, no `maximum`) — the mismatch/PASS/BLOCK_DEPLOYMENT decision logic is unaffected (it is correct for whatever value is actually probed), but the literal golden dollar figures are proven by seeding the probed scenario from the workbook's own declared control case, not by a fully unattended run. See DECISIONS.md D8 for the full explanation and the concrete follow-up (seed `TEST_CANDIDATE_GENERATION` from `RG_CONTROL_CASES` inputs directly).
- **First-divergent-node for the connector path is premium-output-granularity, not a full node-by-node trace diff** between the oracle's trace and the connector's returned trace — see D8.
- **No browser E2E (Playwright) suite exists in this repository** — none was added this session. Backend integration coverage (real API → real Pub/Sub push endpoint → real connector over `httpx.ASGITransport`) is the actual, run, verified proof of the golden-case flows; a true browser-driven E2E suite (upload via a real browser, click through the wizard, observe the decision render) remains open follow-up work. This is flagged, not silently claimed as done.
- **`missions/new/page.tsx` was not modified** — the connector picker and workbook upload were added to `sources/page.tsx` instead (which already had the more complete dual-source upload/compile/launch flow); `missions/new` remains the bundled-demo-sample wizard, unchanged.
- **`POST /connectors/{id}/test` admin route remains unbuilt** (D7's own deferral, confirmed still out of scope — no requirement in this session needed it).
- **Cross-tenant/cross-user access tests were not added** — grep found no tenant/auth-scoping concept enforced anywhere in this codebase's mission/source access paths today (single-tenant demo scope, per the locked doc's own MVP framing). This is a genuine, pre-existing gap, not something this session introduced or silently worked around.

## Session 5 — Authentication, authorization, tenant scoping, runtime config (pre-candidate gate)

**Scope:** code, tests and deployment *configuration* only. Nothing deployed, committed or pushed; no secret deleted; locked doc untouched. Design and rationale: `DECISIONS.md` D9. Route matrix, tenant/legacy policy, worker boundary: `docs/security/AUTHORIZATION_MATRIX.md`. Deployed checklist: `docs/demo/DEPLOYED_ACCEPTANCE_TEST.md` ("AUTH").

Delivered: canonical `RATEGUARD_GEMINI_MODEL=gemini-3.1-flash-lite` / `VERTEX_AI_LOCATION=us` with fail-fast startup validation; API-key path removed from the Gemini client; Firebase Admin token verification via ADC (no private key); roles/tenants from `users/{uid}`; explicit per-route role matrix; tenant stamping and scoping (cross-tenant = 404); legacy records hidden by default; API/worker route separation (`RATEGUARD_SERVICE_ROLE`); explicit CORS, request-size limit, sanitized health; new routes (`/me`, source metadata/download, evidence bundle download, connector test, explanation review); `scripts/bootstrap_user.py`; frontend Firebase login/AuthProvider/AuthGate/authFetch/role-aware nav; deploy script, env files, web build args, `firestore.rules`.

### Verified results
- Backend full suite (`cd backend && python -m pytest -q`): **878 passed, 1 failed** in 19m07s. The one failure (`test_stage_recorder_wiring::…material_drift…`) was caused by earlier sessions' uncommitted supervisor work (explanation/pipeline stages now built, test still asserted "not built"); the assertion was corrected and the file re-run: passing. A later re-run of the auth/config/sources/stage tests together: 275 passed. The full 19-minute suite was not re-run end-to-end after that last test edit and after a `sources.py` fix (restored `_validation_error_detail`, caught by ruff; covered by `test_sources_api`).
- New backend tests: `tests/auth/*` (real-JWT Firebase verifier, authentication, 25-route × 4-role matrix, tenant isolation, worker boundary, CORS/size/health, bootstrap) and `tests/unit/test_runtime_config.py`.
- Frontend: `npm run typecheck` clean; `npm run lint` clean; `npx jest` 58 passed (6 suites); `npm run build` succeeds; `npx playwright test` 7 passed (real Next.js app in Chrome, Firebase Auth REST + API intercepted — not live Firebase).
- Ruff on all new/changed auth files: clean (17 pre-existing findings elsewhere untouched).

### Known limits
See D9 "Not done, by design" and AUTHORIZATION_MATRIX §3–5: no rate limiting, no in-app Pub/Sub OIDC verification, tenant-list scan window, GCS paths not tenant-prefixed, Firestore rules file not published, live Firebase/browser acceptance still to be run on the deployed candidate.


## Session 6 — Release-gap closure (guardrails, tenant-prefixed artifacts, database-level tenancy, rate limiting, rules validation)

**Scope:** code, tests, documentation and deployment *configuration*. Nothing deployed, committed, pushed or published; no cloud resource or secret touched; locked doc untouched. Rationale: `DECISIONS.md` D10. Policy detail: `docs/security/AUTHORIZATION_MATRIX.md` §3, §6, §7.

### Verified results (final worktree, run after the last code change)
- **Backend full suite** with a real Firestore emulator and `RATEGUARD_REQUIRE_EMULATOR=1` (`backend/scripts/test_with_emulator.sh`): **1027 passed, 0 failed, 0 skipped**, 16m19s. This includes the workbook-security, connector contract/SSRF, authentication/RBAC/tenant, artifact-isolation, guardrail, rate-limit (incl. 16-thread concurrency on Firestore) and evidence tests.
- **Ruff** (`app tests scripts`): all checks passed. No type checker (mypy/pyright) is configured in this repository, so none was run.
- **Firestore rules emulator suite** (`infrastructure/firestore-rules-tests`): **114 passed**. Negative control (permissive rules): 43 failed, confirming the tests detect leaks. Rules were **not** published.
- **Frontend:** typecheck clean; lint clean; Jest **59 passed** (6 suites); production build compiled; Playwright **7 passed**.

### Session-6 findings fixed along the way
- Rate-limit counter ids joined identity fields with `|`: two different identities could collide (caught by a new test; now JSON-encoded before hashing).
- Local artifact store wrote to `base_dir/<upload filename>` (path traversal); paths are now derived only from a validated `ArtifactKey`.
- Guardrail variables were never read by runtime code (module constants); now canonical `RATEGUARD_*` variables.
- Ruff `--fix` also normalized import order in a few previously-modified files and removed 2 unused variables; no behaviour change.

### Known limits (unchanged unless stated)
Indexes/TTL/rules are not applied (manual, reviewed step); Firestore documents lacking `tenant_id` entirely are hidden from lists (see matrix §3.4); no edge rate limiting for unauthenticated floods; in-app Pub/Sub OIDC verification not implemented; Playwright uses intercepted Firebase/API, not live services; the live deployed AUTH checklist (A-1 to A-20) is still to be run.
