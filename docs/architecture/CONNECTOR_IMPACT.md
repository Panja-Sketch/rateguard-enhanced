# Connector-backed portfolio impact

Source A is an authoritative controlled workbook (compiled to strict IPIR). Source B is a
versioned black-box REST rating engine (the *connector*). No candidate IPIR exists locally.
For every eligible masked portfolio row RateGuard prices the policy **locally** through the
authoritative IPIR (expected premium) and **remotely** through the connector (candidate
premium), compares them with Decimal arithmetic, and aggregates the affected policies and
financial exposure. Definitions match the package-vs-package analysis (`delta = candidate −
expected`; *overcharge* when `delta > 0`, *undercharge* when `delta < 0`).

## Architecture

```
mission worker (Pub/Sub push, private)            impact-batches topic            worker (same private service)
 └─ supervisor → ConnectorImpactCoordinator ──publish batch refs──▶ impact-batches-worker-sub ──OIDC push──▶ /internal/pubsub/impact-batch
      plan job + batches (Firestore)                                                                       BatchProcessor.process()
      dispatch with back-pressure  ◀────────── poll Firestore checkpoints ◀──── lease → price rows → fenced checkpoint write
      finalize (first-writer-wins)                                             DLQ: impact-batches-dead-letter (5 attempts)
```

* **Job identity** `IJ-<sha256(tenant | mission | attempt | snapshot sha | source sha | connector | version | batch size | row limit)>`.
  A redelivered mission resumes the same job; a mission *retry* (new attempt) or a changed
  snapshot/source/connector starts a fresh one.
* **Batches** are deterministic slices `[n·B, (n+1)·B)` of the snapshot (file order). A batch
  message carries only `tenant_id`, `job_id`, `batch_no`.
* **Leasing / idempotency** (`app/impact/store.py`): `lease_batch` is an atomic compare-and-set;
  the lease owner is a fencing token — `complete_batch` is rejected unless the caller still holds
  the lease, and a `DONE` batch is immutable. Duplicate or redelivered messages return
  `DUPLICATE`/`CLOSED` and are acknowledged; they cannot double-count.
* **Resume**: a batch that finished with transient failures is `INCOMPLETE`; its next attempt
  re-prices **only** the unresolved rows and merges into the retained results. A crashed handler
  releases its lease without changing retained results.
* **Back-pressure**: at most `MAX_INFLIGHT_BATCHES` outstanding; stalled dispatches are re-sent
  after `batch_timeout + 90 s` (safe because handling is idempotent).
* **Liveness**: the coordinator refreshes the mission's `last_heartbeat_at` each poll (field-level
  Firestore update) so the long scan is never mistaken for a dead worker. Wait time is capped below
  the push subscription's 600 s ack deadline.
* **Finalization** is a Firestore transaction: the first writer wins; later/duplicate finalizers
  return the stored aggregate.
* **Cancellation**: the mission's cancel flag is polled; the coordinator marks the job cancelled and
  in-flight batches stop at their next chunk. The impact status is `CANCELLED` (never `PASS`).

## Retry classification

Transient (retried with capped, jittered exponential backoff, then batch-level re-attempts up to
`MAX_BATCH_ATTEMPTS`): HTTP 429, 5xx, timeouts, transport errors, item error
`TEMPORARILY_UNAVAILABLE`/`RATE_LIMITED`. Permanent (never retried without a configuration
change): authentication/authorization, engine-version, schema/redirect/request-id violations and
other 4xx. A per-batch circuit breaker opens after `BREAKER_THRESHOLD` consecutive infrastructure
failures (half-open after cooldown) and trips permanently on a systemic permanent failure; after 3
batches that failed entirely with permanent errors the coordinator halts the scan
(`CONNECTOR_PERMANENT_FAILURE`).

## Completion semantics (locked)

| Situation | Impact | Mission decision |
|---|---|---|
| complete scan, no mismatch | `COMPLETE` | may `PASS` |
| ≥1 proven mismatch | `COMPLETE`/`PARTIAL` | `BLOCK_DEPLOYMENT` (exposure labelled a **lower bound** when partial) |
| no proven mismatch, incomplete scan | `PARTIAL` | `REVIEW_REQUIRED`, never `PASS` |
| connector outage / auth failure, inconclusive | `PARTIAL` or `NOT_RUN` | `REVIEW_REQUIRED` |
| cancelled | `CANCELLED` | never `PASS` |
| unhandled internal failure | – | mission `FAILED` |

Zero impact is never inferred from missing or incomplete data. Budget exhaustion (rows, wall time)
yields `PARTIAL` + `REVIEW_REQUIRED` unless a mismatch already proves `BLOCK_DEPLOYMENT`.

**Out of scope vs. inconclusive.** A policy whose effective date lies outside the authoritative
source's effective period has no authoritative expected premium (the calculation-date resolver
rejects it with `CALCULATION_DATE_OUT_OF_PERIOD`). Such rows are reported as **out of scope**
(reason `OUTSIDE_EFFECTIVE_PERIOD`), excluded from `eligible`, and disclosed — they are not
priced and never counted as matches. For the shipped synthetic portfolio and the golden workbook
(effective 2026-10-01) that is 12,467 of 50,000 policies (September effective dates).
`inconclusive` is reserved for rows that *should* have been comparable but were not.

Invariant (tested): `successful_comparisons + inconclusive + out_of_scope + unprocessed = rows_total`.

## Metrics computed

processed / eligible / out-of-scope / successful comparisons / mismatches / inconclusive /
unprocessed; overcharge & undercharge count and value; signed net delta; absolute exposure; mean,
median, min, max of affected deltas (signed and absolute); percentage affected; 30/60/90-day renewal
impact (as-of date pinned at job creation); cohort distribution over the *compared* policies with the
existing minimum-size suppression; retries, requests, batch time, error classes, breaker state.

## Privacy and tenancy

Only approved masked rating fields (`territory, roof_age, deductible, protection_class,
construction_type, dwelling_limit, multi_policy, claims_free, claims_free_years`), intersected with the
inputs the authoritative package declares, ever reach the connector. The policy identifier is never
sent; connector requests use opaque ids (`<job>:r<row>`). Checkpoints store row **indexes**, not
identifiers. Every job/batch document carries `tenant_id` and is keyed `{tenant}--{job}`; another
tenant's job is indistinguishable from a missing one. Request bodies and rows are never logged.

## Runtime budgets (validated at startup — invalid values abort the process)

`RATEGUARD_IMPACT_*`: `MAX_POLICIES` (50 000), `BATCH_SIZE` (200, ≤250), `MAX_INFLIGHT_BATCHES` (3) ×
`REQUEST_CONCURRENCY_PER_BATCH` (5) ≤ 20 global concurrent requests, `MAX_QPS` (120),
`REQUEST_TIMEOUT_SECONDS` (10), `BATCH_TIMEOUT_SECONDS` (240), `MAX_RETRY_ATTEMPTS` (3),
`MAX_BATCH_ATTEMPTS` (3), `MAX_MISSION_SECONDS` (540, must exceed the batch timeout),
`MAX_MISMATCH_EXAMPLES` (20), `MAX_EVIDENCE_BYTES` (512 000), `PREMIUM_TOLERANCE` (0.00). Consumption is
recorded in the mission's impact evidence (`budget`, `budget_exhausted`) without any request payload.

## Optional batch-quote capability (`quote-batch-v1`)

A connector may advertise `GET /capabilities → {"quote_batch": {"schema_version": "quote-batch-v1",
"max_items": N}}` and accept `POST /quote/batch` with up to N independent items (`item_id`,
`effective_date`, `transaction_type`, `inputs`). Each item returns `OK` + decimal-string outputs or a
per-item `ERROR`; items correlate by `item_id` one-to-one. Same TLS/SSRF/no-redirect/ID-token controls,
512 KiB request cap. Connectors without the capability are driven with concurrent single quotes (a
50 000-row scan then needs ≈`rows / MAX_QPS` seconds, so raise `MAX_QPS`/the mission budget or accept a
`PARTIAL`/`REVIEW_REQUIRED` outcome). Measured on the demo engine in-process: 50 000 rows in ≈21 s with
batch quote.

## Storage

Firestore `impact_jobs/{tenant}--{job}` (+ `batches/{n}` with TTL on `expires_at`, 90 days). No
composite index is required (mission lookup uses two equality filters). Firestore rules deny every
client; only the API/worker identities reach it.
