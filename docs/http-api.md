# HTTP API

FastAPI exposes the shared investigation engine for trusted local use. Start the
application with the [Docker quick start](../README.md#quick-start), then open
[Swagger UI](http://localhost:8000/docs) for request fields, response schemas and
interactive requests. This link works only while your local API is running.

The API has no authentication; keep it on localhost.

## Endpoints

These are backend paths. The browser uses an `/api` prefix that the proxy removes.

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/health` | Check that compatible database storage is ready |
| POST | `/investigations` | Create a paused investigation; return 201 and its UUID |
| GET | `/investigations/{identifier}` | Read saved state, evidence, diagnosis and attempt counts |
| POST | `/investigations/{identifier}/resume` | Continue until completion, pause or failure |
| POST | `/investigations/{identifier}/pause` | Request a pause at a saved boundary |

See [agent workflow and recovery](workflow.md) for saved state, budgets and crash recovery.

## Optional runbook retrieval

Responses include optional runbook retrieval fields:

| Field | Meaning |
| --- | --- |
| `retrieval` | Saved retrieval configuration; `null` when disabled |
| `state.retrieved_sources` | Retrieved runbook passages, separate from incident evidence |
| `state.diagnosis.source_ids` | References to retrieved passages cited by the diagnosis |

## Error codes

| HTTP | Code | Meaning |
| --- | --- | --- |
| 413 | `request_too_large` | Body exceeds 100,000 bytes |
| 422 | `invalid_request` | Invalid JSON, fields, UUID or input limits |
| 404 | `investigation_not_found` | UUID has no saved investigation |
| 409 | `investigation_busy` | Another client is executing this UUID |
| 409 | `investigation_failed` | Saved investigation has failed |
| 409 | `incompatible_runtime` | Saved unfinished work cannot run with this version |
| 409 | `recovery_required` | Saved state needs recovery; inspect before starting new work |
| 502 | `provider_error` | Model request failed |
| 502 | `execution_validation_error` | Execution produced invalid state/output |
| 503 | `configuration_unavailable` | Required backend configuration is missing or invalid |
| 503 | `storage_unavailable` | Database is unavailable, uninitialized or incompatible |
| 500 | `execution_error`, `internal_error` | Unexpected execution or server failure |
