# Production acceptance record — commit `f2df35d48d820a11762654d25b10f63809e87aa1`

Synthetic demonstration data only. This records what was verified live in project
`rateguard-enhanced` (us-central1) after the connector-audience repair, the black-box
rating engine, the mission-name field and the server-derived source hash.

## Production revisions (100% traffic each)

| Service | Revision | Image digest | Service account |
| --- | --- | --- | --- |
| Rating engine (private) | `rateguard-rating-engine-00019-mab` | `sha256:76da6ed3b827b6686fd96781fb539c68b577f6f128eda53a2c79ddc579322d10` | `rateguard-rating-engine-sa` (no project roles) |
| Worker (private) | `rateguard-worker-00040-64t` | `sha256:278eea41561bb7caa0e1d1ff1c5d3ea335f93b2015728b5680e459c935e5efb2` | `rateguard-worker-sa` |
| API (public, Firebase-authenticated) | `rateguard-api-00048-mch` | `sha256:278eea41561bb7caa0e1d1ff1c5d3ea335f93b2015728b5680e459c935e5efb2` | `rateguard-api-sa` |
| Web (public) | `rateguard-web-00019-vuw` | `sha256:4eab1a992f3e69894a6277776ed51f60d8516f823e41298ccab0977fa31b2664` | `rateguard-web-sa` |

Production URL: <https://rateguard-web-nwhotixfva-uc.a.run.app>

The worker/API digest is tied to the commit three ways: it is the digest pinned in the
verification evidence, it is the digest of the live API revision, and it is what the
Artifact Registry tag `rateguard-api:candidate-f2df35d48d820a11762654d25b10f63809e87aa1`
resolves to (that tag is created by the canonical build from that exact SHA). No image was
rebuilt during promotion.

## Live missions

| Mission | Selection | Result |
| --- | --- | --- |
| `MIS-BC2F024B` (candidate verification) | golden workbook vs `defective-v1` | BLOCK_DEPLOYMENT, 8 match / 3 mismatch, marker `[CANDIDATE-VERIFY-f2df35d48d82]` |
| `MIS-927A58D7` (production) | golden workbook vs `canonical-v1` | **PASS**, 11/11 valid matches, 0 inconclusive |
| `MIS-63029F1E` (production) | golden workbook vs `defective-v1` | **BLOCK_DEPLOYMENT**, mismatches at roof age 25 (control case), 21 and 22: 700.00 approved vs 655.00 deployed |

Each mission: 11 connector invocations, all `SUCCESS`, no auth denial, no inconclusive probe;
worker revision, digest and git SHA recorded in `deployment.json`; Source A content hash
`4042ba5e…dc2b` (server-derived); evidence-bundle manifest hashes verify; no secret or PII
patterns in the bundle.

## Production controls checked

- Anonymous: web 200; API `/me` and `/missions` 401; worker 403; rating engine 403.
- CORS allows only `https://rateguard-web-nwhotixfva-uc.a.run.app`; a foreign origin receives no CORS header.
- Rating-engine `roles/run.invoker`: only `rateguard-api-sa` and `rateguard-worker-sa`. Worker invoker: only `rateguard-worker-sa` (its own Pub/Sub push identity).
- Connector endpoint and ID-token audience are both the stable engine URL
  `https://rateguard-rating-engine-nwhotixfva-uc.a.run.app` (API and worker).
- Pub/Sub: `assurance-runs-worker-sub` and `impact-batches-worker-sub` push to the stable worker URL
  with the stable worker URL as OIDC audience, as `rateguard-worker-sa`; dead-letter topics intact;
  no leftover verification topics or subscriptions.
- Real Firebase login, application load, API LIVE, demo account and Admin role confirmed in a browser.

## Rollback (prior production revisions)

API `rateguard-api-00027-giy`, worker `rateguard-worker-00026-gof`, engine
`rateguard-rating-engine-00012-nom`, web `rateguard-web-00012-pox`
(`infrastructure/rollback.sh`; it prints the `update-traffic` commands to run).

## Known limitations

See "Known deployment-tooling limitations" in
[DEPLOYMENT_RUNBOOK.md](../operations/DEPLOYMENT_RUNBOOK.md). None affects runtime security,
authorization, rate limiting, data routing, monitoring, evidence or rollback.

This proves the vendor-neutral REST integration pattern. It is not a certified native
Guidewire or Duck Creek adapter.
