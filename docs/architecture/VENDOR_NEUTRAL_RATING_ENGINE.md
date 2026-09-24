# Vendor-neutral, black-box rating-engine verification

RateGuard AI verifies that an independently deployed rating implementation still
prices the way its approved specification says it should — without knowing anything
about how the implementation is built.

```
 Controlled RateGuard workbook           RateGuard AI                 RateGuard Demo Insurer Rating Engine
 (approved actuarial intent)   ──────▶  compile → IPIR → oracle        (black-box REST reference engine)
                                          plan probes ─────────────────▶  POST /quote, /quote/batch
                                          compare premiums ◀────────────  versioned REST contract only
                                          PASS | BLOCK_DEPLOYMENT | REVIEW_REQUIRED
```

## The four roles

1. **The controlled workbook represents approved actuarial intent.** It is the
   specification RateGuard trusts. RateGuard compiles it to its own IPIR and derives
   the expected premium for every probe with its independent oracle.
2. **The private rating-engine service represents independently deployed
   implementation behaviour.** It is a separate Cloud Run service with its own
   Docker image, its own dependencies and its own service account (which holds no
   IAM roles). Its rating tables live in one small file,
   [`backend/rating_engine/engines/versions.py`](../../backend/rating_engine/engines/versions.py).
3. **RateGuard knows only the REST contract.** It reaches the engine through an
   administrator-registered connector (`connector_id` + `engine_version`, never a
   URL), over HTTPS, with a Google ID token minted from RateGuard's own service
   identity. The engine advertises what it supports at `GET /capabilities`
   (engine versions, batch capability, product, and non-sensitive provenance:
   implementation version, source commit, image digest, Cloud Run revision).
4. **A developer can change the engine without changing RateGuard.** Fix or alter
   a factor in `versions.py`, redeploy the engine, re-run the same mission:
   RateGuard's code, tests and configuration are untouched.

RateGuard then returns its own verdict:

| Verdict | Meaning |
| --- | --- |
| `PASS` | Every valid probe matched the approved specification. |
| `BLOCK_DEPLOYMENT` | A proven premium mismatch (e.g. $700.00 approved vs $655.00 deployed at roof age ≥ 21). |
| `REVIEW_REQUIRED` | The comparison could not be completed (connector unavailable, denied, timed out, contract violation). Probes are *inconclusive*: this is never a PASS and never a claim of a pricing defect. |

## Why it is genuinely black-box

* The engine package imports nothing from RateGuard (`app.*`): not the oracle, IPIR
  evaluator, workbook compiler, test planner, comparison or decision code. This is
  enforced by `backend/tests/rating_engine/test_isolation.py`, which parses every
  engine module, boots the engine in a fresh interpreter in which `app` cannot be
  imported, and inspects the Dockerfile (it copies only `backend/rating_engine`,
  with its own `requirements.txt`; RateGuard's `app/` and `data/` are not in the image).
* Its answers are cross-checked against RateGuard's independent oracle for both
  versions across roof ages 0–120 (`test_engine_contract.py`) — agreement comes from
  two implementations, not shared code.
* Request models reject unknown fields, so a caller cannot supply a tenant, user or
  authorization claim; authorization is Cloud Run IAM in front of the service.
* It serves two implementation versions: `canonical-v1` (the intended AZ HO3
  result, $700.00 for the golden case) and `defective-v1` (a realistic mis-keyed
  factor for roof age ≥ 21, giving $655.00).

## Connector authentication

The request endpoint and the Google ID-token **audience** are separate settings
(`RATEGUARD_RATING_ENGINE_CONNECTOR_BASE_URL` / `…_AUDIENCE`). A candidate deployment
calls a traffic-tagged URL but must present a token whose audience is the stable
service URL; deriving the audience from the endpoint makes Cloud Run reject the token
(HTTP 401), which is exactly how every probe once became `CONNECTOR_FAILURE`.
Startup fails closed on a missing or inconsistent configuration. No API key, Firebase
Admin JSON or shared secret is involved. See the
[deployment runbook](../operations/DEPLOYMENT_RUNBOOK.md).

## Honest limitation

**This proves the vendor-neutral REST integration pattern. It is not a certified
native Guidewire or Duck Creek adapter.** No native vendor adapter exists, and the
demo engine is a synthetic reference implementation, not any vendor's product. Repository
extraction from a developer's source control is not implemented either: the demo works
by redeploying the engine and re-running a mission.
