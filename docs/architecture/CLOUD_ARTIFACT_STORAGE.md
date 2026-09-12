# Google Cloud Storage Artifacts & Evidence Architecture

## Overview

RateGuard AI stores source files, compiled IPIR packages, test suites, execution logs, and evidence lineage in an enterprise storage layer supporting both local filesystem development and Google Cloud Storage (GCS).

---

## Artifact Storage Model

```
gcs://rateguard-ai-artifacts/  (or local RATEGUARD_ARTIFACT_DIR)
├── sources/
│   └── json/       # Raw uploaded actuarial JSON specs (the only format
│                   # accepted today -- .xlsx/.pdf uploads are rejected
│                   # before reaching storage, see SOURCE_ADAPTERS.md)
├── ipir/           # Compiled IPIR packages
├── portfolios/     # Synthetic 50k policy CSV files
└── evidence/       # Assurance run evidence records & logs
```

---

## Configuration & Environment Variables

| Variable | Default Value | Description |
| :--- | :--- | :--- |
| `RATEGUARD_ARTIFACT_STORE` | `local` | Store provider: `local` or `gcs` |
| `RATEGUARD_ARTIFACT_DIR` | `./data/artifacts` | Directory for local artifact storage |
| `RATEGUARD_GCS_BUCKET` | `rateguard-ai-artifacts` | Target GCS bucket for cloud storage |

---

## Interface Abstraction

The system interacts with artifacts strictly through `BaseArtifactStore`:

- `save_artifact(descriptor, content)`
- `get_artifact(artifact_id)`
- `get_artifact_content(artifact_id)`
- `list_artifacts(category)`
- `delete_artifact(artifact_id)`

`LocalArtifactStore` handles local development; `GCSArtifactStore` connects to Google Cloud Storage when deployed.

