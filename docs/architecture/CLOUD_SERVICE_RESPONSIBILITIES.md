# Cloud Service Responsibilities Matrix

| Service | Primary Responsibility | Content Stored / Executed |
| :--- | :--- | :--- |
| **Cloud Storage** | Binary & Text Artifact Store | Uploaded JSON sources (the only format accepted today), compiled IPIR JSON packages, generated HTML audit reports. |
| **Cloud Firestore** | Workflow State & Evidence Metadata | Assurance run status, workflow stage progress, audit event timeline, evidence lineage records. |
| **Google BigQuery** | Portfolio Analytics Store | 50,000 synthetic policy records, structured SQL predicate filters, exposure result summaries. |
| **Cloud Pub/Sub** | Asynchronous Job Queue | Light job messages (`AssuranceJob`: `run_id`, `job_id`, `source_id`). No raw file payloads. |
| **Cloud Run** | Runtime Execution Engine | FastAPI REST API (`rateguard-api`), Pub/Sub worker subscriber (`rateguard-worker`), Next.js frontend (`rateguard-web`) — all deployed. |
| **Vertex AI / Gemini** | Structured-Decision Supervisor | Bounded, schema-validated decisions at fixed pipeline stages via the Google GenAI SDK — never raw arithmetic or free-form agent chatter. |

