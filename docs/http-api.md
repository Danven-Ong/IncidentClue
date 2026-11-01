# Local HTTP investigation service

## Contract

FastAPI is a transport adapter over the same durable lifecycle used by the CLI.
The CLI remains supported. The HTTP service is for trusted local use, without
authentication; publish its port on localhost only. CORS is disabled. No frontend,
retrieval, queues, pooling, automatic retries, streaming or deployment is included.

| Method | Path | Success |
| --- | --- | --- |
| GET | `/health` | 200 when application and checkpoint storage are initialized and readable |
| POST | `/investigations` | 201 initial paused inspection; `Location` points to inspection |
| GET | `/investigations/{id}` | 200 observed inspection envelope, including failed runs |
| POST | `/investigations/{id}/resume` | 200 on completion or pause; completed runs return saved output with zero new calls |
| POST | `/investigations/{id}/pause` | 200 observed inspection after a cooperative pause request |

Creation takes `{"incident": {...}, "health": {...}, "logs": null, "model": null}`.
Incident and health are required; logs/model may be omitted or null. Snapshots
use the existing models (`service` and `evidence`), not filesystem paths. Model
resolves to the server's `GROQ_MODEL` or default at creation and is saved. Resume
never replaces saved inputs/model. Creation saves initial state, always pauses,
and reserves zero model/tool attempts. It needs no provider credentials.

Resume accepts optional `pause_after=decide|execute|diagnose|validate`. It runs
synchronously until completion, pause or error. Completion wins at the final
boundary. Ordinary synchronous handlers run in worker threads; each operation
owns and closes its application/checkpointer connections in that thread. Session
advisory locks exclude competing HTTP and CLI executors. No connections are
shared between requests and no background jobs are enqueued.

A lost response does not roll back work. Client disconnection is not a pause or
cancellation guarantee; execution may continue while the server survives. Inspect
the known UUID before retrying resume. Creation response delivery is not guaranteed;
retrying creation can create another UUID (no request idempotency key support).
After process death, safe checkpoint/cached-output recovery or uncertain-attempt
refusal follows the existing lifecycle. There is no automatic restart execution
and no exactly-once external-call guarantee.

Inspection needs no provider key and does not acquire execution ownership. Its
sequential reads may observe advancing state; it is not an atomic snapshot.
Pause requests are cooperative: an in-flight node can finish. The response can
still say running and pause_requested=true; terminal runs retain stored values.

## Error contract

All errors use `{"error":{"code":"...","cause":null,"message":"..."}}`.
Execution errors for a known investigation also include `investigation_id` so
the client can inspect saved state and charged attempts. Messages are controlled;
raw validation inputs, exception strings, DSNs and provider diagnostics are omitted.

| HTTP | Code | Meaning |
| --- | --- | --- |
| 413 | `request_too_large` | Body exceeds 100,000 bytes, including streamed/chunked bodies |
| 422 | `invalid_request` | Invalid JSON/schema/UUID/boundary/model or existing input bounds |
| 404 | `investigation_not_found` | Valid UUID has no record |
| 409 | `investigation_busy` | Another executor holds the session lock |
| 409 | `investigation_failed` | Already failed; cause is the stored lifecycle error code |
| 409 | `incompatible_runtime` | Unfinished work uses a different runtime fingerprint |
| 409 | `recovery_required` | Uncertain attempts or lifecycle inconsistencies; cause retains the lifecycle classification |
| 502 | `provider_error` | Controlled provider request failure; stored investigation is failed and reservations remain charged |
| 502 | `execution_validation_error` | Execution could not produce valid state/output; failed work remains inspectable |
| 503 | `configuration_unavailable` | Server database/model/provider configuration required for this operation is unavailable |
| 503 | `storage_unavailable` | Database unavailable, uninitialized or schema incompatible |
| 500 | `execution_error` | Execution failed unexpectedly; saved progress remains inspectable |
| 500 | `internal_error` | Unexpected server defect, sanitized |

Requests have bounded bodies and existing incident/snapshot byte limits. A model
must be nonblank and at most 200 characters. Unknown wrapper fields are rejected.
Providers are created lazily: create, inspect, pause and completed resume work
without a model key. HTTP status reports the operation, not diagnostic certainty.
Synthetic observations and model hypotheses are not production-quality guarantees.

Storage initialization is explicit (`incidentclue db-init`). Health checks read
both application and checkpoint tables without migrations, writes or model calls.
The server stays available to report 503 during an outage and recovers on later
requests when storage returns. Readiness does not validate provider credentials.

The HTTP subpackage is outside the existing nonrecursive source fingerprint scan.
Shared lifecycle error classification changes in this release do change that
fingerprint. Older unfinished runs remain inspectable but refuse incompatible
resume. No history is deleted and no database reset/migration is performed.

## Verification boundary

Deterministic fake transports test CLI/HTTP state, output and budget parity (not
UUID/timestamp equality), completed resume, invalid/missing/terminal/incompatible
IDs, cooperative pause, contention, credential-free reads and sanitized failures.
Real PostgreSQL tests exercise stored checkpoints and API/CLI ownership. Process
crash tests cover safe replay and conservative refusal. CI retains package,
Linux/Windows, PostgreSQL and container/Compose checks without live provider calls.

## Run locally

Install using the existing development lockfile instructions in the backend
README. Keep your existing `.env`; add missing settings from `.env.example`
without overwriting provider credentials. Neither app import nor app creation
searches for an env file implicitly.

```sh
docker compose --profile web up -d --wait postgres
incidentclue db-init --env-file .env
python -m uvicorn incidentclue.api.app:create_app --factory --host 127.0.0.1 --port 8000 --env-file .env --no-access-log
```

Open `/docs` on the local server for the generated API schema and interactive
requests. A minimal creation body (empty health evidence means unavailable data,
not a healthy service) is:

```json
{
  "incident": {"incident_id": "demo", "service": "checkout", "summary": "Startup failed", "evidence": []},
  "health": {"service": "checkout", "evidence": []}
}
```

POST this JSON to `/investigations`, copy the returned investigation_id, then
POST `/investigations/UUID/resume?pause_after=decide`. GET the inspection URL to
read state; POST its `/resume` suffix to continue, or `/pause` to request a stop
from another client. Supplying recorded health/log evidence uses the same JSON
models as `data/synthetic/checkout-health.json` and `checkout-logs.json`.

For the container service, the explicit CLI initialization can instead run in
Compose. The host CLI uses localhost:55432; the Compose CLI/API use postgres:5432:

```sh
docker compose build web
docker compose run --rm durable db-init
docker compose --profile web up -d --wait web
```

The API is published at `http://127.0.0.1:8000`; override `INCIDENTCLUE_WEB_PORT`
if occupied. PostgreSQL uses the existing `postgres-data` volume. `.env` is a
read-only provider configuration mount; the container DSN comes from the Compose
environment and takes precedence over any host DSN in that file. Override
`INCIDENTCLUE_COMPOSE_DATABASE_URL` when changing the database/user/password.
Provider/model settings are loaded at server creation; restart the server to
apply changes. Saved investigations retain their original model.

`docker compose --profile web stop web postgres` stops work while retaining
volumes. Never use `down -v` to pause work: it deletes stored investigations.
Starting the web service against uninitialized storage reports unhealthy/503;
run explicit db-init, then retry readiness. Health does not need a provider key.
Long resumes occupy a worker thread and two DB connections; thread capacity is
finite. This local service has no throughput claim or public-access controls.
