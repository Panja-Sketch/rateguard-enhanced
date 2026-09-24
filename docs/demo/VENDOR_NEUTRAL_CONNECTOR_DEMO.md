# Vendor-neutral connector demo — developer guide

Shows RateGuard verifying an independently deployed rating engine against the
approved workbook, and returning a verdict without any RateGuard code change.
Background: [VENDOR_NEUTRAL_RATING_ENGINE.md](../architecture/VENDOR_NEUTRAL_RATING_ENGINE.md).

> **Limitation.** This proves the vendor-neutral REST integration pattern. It is not
> a certified native Guidewire or Duck Creek adapter, and there is no automatic
> repository extraction: the "developer change" is editing the engine and redeploying it.

## What you change (and what you don't)

| | |
| --- | --- |
| **Engine-owned file you edit** | [`backend/rating_engine/engines/versions.py`](../../backend/rating_engine/engines/versions.py) — the `ROOF_AGE_TIERS` table (one `RoofAgeTier(...)` line per tier per engine version). |
| **The one line that is "the defect"** | `RoofAgeTier(21, None, Decimal("1.31"))` under `"defective-v1"` — should be `1.40` (it prices roof age ≥ 21 at $655.00 instead of $700.00). |
| **RateGuard code changed** | **None.** No file under `backend/app`, `frontend`, or the connector registry changes. RateGuard selects an engine *by name* (`connector_id` + `engine_version`) and never sees the engine's source. |

## Inputs

* Approved specification: `data/samples/workbook_v1/canonical/AZ_HO3_GOLDEN_workbook.xlsx`
  (product `az_ho3`, jurisdiction `AZ`, effective `2026-10-01`, expected premium `700.00`,
  11 planned probes).
* Connector: `rating-engine-demo` with engine version `canonical-v1` or `defective-v1`.

(A generic workbook such as `rateguard-workbook-sample.xlsx` is not contract-compatible with the
AZ HO3 engine and is not expected to pass.)

## Scenarios

In the web app: **Sources** → upload the golden workbook → compile → **New mission** →
Mode *Release conformance* → Source A = the compiled workbook, Source B = *API connector*
`rating-engine-demo` with the version below.

### A — Conformant release (`canonical-v1`) → `PASS`

All 11 probes are valid comparisons and match, including the roof-age 20/21/22 boundary.
`experiments.mismatch_count = 0`, `inconclusive_count = 0`.

### B — Defective implementation (`defective-v1`) → `BLOCK_DEPLOYMENT`

Mismatches include roof age 21 and 22 and the workbook control case (roof age 25):
**approved 700.00 vs deployed 655.00**. Probes at roof age ≤ 20 still match, which
localises the defect to the ≥ 21 tier.

### C — Connector unavailable → `REVIEW_REQUIRED`

Make the engine unreachable for the caller *without exposing it publicly* (for example
revoke the calling service account's `roles/run.invoker` on the engine for the
duration of a test on a candidate, or use the automated test below). Every probe is
`INCONCLUSIVE`, `mismatch_count = 0`, and the UI says *"No valid premium comparisons
were completed because the connector was unavailable."* — never "zero diffs", never PASS,
never a pricing-defect claim. Failures are classed as `CONNECTOR_AUTH_DENIED`,
`CONNECTOR_TIMEOUT`, `CONNECTOR_CONTRACT_ERROR`, `CONNECTOR_VERSION_UNSUPPORTED` or
`CONNECTOR_UNAVAILABLE` (evidence field `error_class`).

### D — Unsupported engine version → `422`

Selecting an unregistered version (for example `canonical-v99`) is refused when the
mission is created: HTTP **422**, issue code `CONNECTOR_ENGINE_VERSION_NOT_ALLOWED` on
`source_b`. An unregistered connector id gives `CONNECTOR_NOT_REGISTERED`. The browser can
only send a `connector_id` and an `engine_version`; the mission model has no URL field,
so no arbitrary endpoint can be supplied.

## Show the developer change (the "RateGuard is unchanged" moment)

1. Run scenario B against `defective-v1` → `BLOCK_DEPLOYMENT`.
2. In `versions.py`, change the `defective-v1` top tier factor from `1.31` to `1.40`.
3. Redeploy **only** the rating-engine service (`deploy_candidate_enhanced.sh` rebuilds all
   images from one commit; the engine is the only service whose behaviour changes).
4. Re-run the same mission → `PASS`.

`test_a_developer_change_to_the_engine_alone_flips_the_verdict` automates steps 1–4
in-process. Revert the factor afterwards so `defective-v1` keeps demonstrating the defect.

## Reproduce locally (no cloud)

```bash
cd backend
python -m pytest tests/integration/test_vendor_neutral_demo_scenarios.py tests/rating_engine -q
```

These run the real API path (upload → compile → mission → worker → evidence) with the
real planner; only the network transport to the engine is in-process.

## Verify the deployed engine

```bash
# Anonymous access must be denied (private service): expect 403
curl -s -o /dev/null -w '%{http_code}\n' https://<engine-service-url>/capabilities
```

An authenticated caller (RateGuard's service accounts) receives, at `GET /capabilities`,
the supported engine versions and provenance (`implementation_version`, `source_commit`,
`image_digest`, `deployment_revision`) — no secret or request data.
