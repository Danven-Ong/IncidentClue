# Durable investigation lifecycle

## Feature contract

Start, inspect, deliberately pause, and resume a bounded investigation across
process restarts using PostgreSQL. The CLI stays supported. Existing `diagnose`
and `investigate` commands keep their one-shot behavior and need no database.

This feature excludes API/frontend, retrieval/pgvector, production connectors,
queues, deployment, hosted tracing, automatic retry, arbitrary checkpoint editing,
and automatic migration of incompatible graph state. FastAPI over this same engine
belongs to the next feature PR. No exactly-once physical execution is promised.

## Commands and identity

New commands are `db-init`, `start`, `inspect ID`, `pause ID`, and `resume ID`.
Use an explicit `--env-file` or process environment for `INCIDENTCLUE_DATABASE_URL`.
Database credentials are runtime configuration, never graph state.

- `db-init` explicitly initializes application and supported PostgreSQL checkpointer
  tables; repeated initialization is safe. An incompatible existing application
  schema is rejected before its version records or application tables are changed.
  Normal commands never silently migrate.
- `start` validates incident, synthetic snapshots and model, assigns a fresh UUID,
  and durably saves inputs/settings before execution. This investigation ID is
  also the LangGraph thread ID, distinct from the user-supplied incident ID.
- `start --paused` saves initial state without a provider request. `--pause-after
  NODE` on start/resume provides a repeatable demonstration boundary; node is one
  of decide, execute, diagnose, validate. If execution completes there, completion
  takes precedence over pausing.
- `inspect` reads status, checkpoint/next-node information, evidence, history,
  diagnosis when available, and both checkpoint counters and charged attempt
  totals. It requires no provider credentials and performs no model/tool calls.
- `pause` persists a cooperative request, including from another process. A running
  node may finish; no further node begins once its request is observed at a
  committed boundary. Paused/ready runs can be paused idempotently. Terminal runs
  retain their status. No process signals or background worker are required.
- `resume` takes exclusive execution ownership, restores saved state and inputs,
  and continues the next node. It never injects initial state into an existing
  thread. Completed IDs return the saved result without executing. Failed IDs
  reject execution and remain inspectable. Missing/invalid IDs fail clearly.

Execution is synchronous in the start/resume process. Statuses are `ready`,
`running`, `paused`, `completed`, `failed`, and `recovery_required`. A database
session advisory lock, held by the executing process, rejects concurrent resumes.
Lock release on process death allows recovery; a stale `running` status is not
proof of a live executor. Inspect reports execution ownership separately.
Ownership inspection reads PostgreSQL's lock catalog without acquiring an
execution lock, so inspection does not temporarily reject a real executor.
PostgreSQL may observe a socket disconnect shortly after the local process exits;
until then a resume correctly reports busy. Retry the command after ownership is
released; the busy command executes no nodes or provider calls.

## Recovery, compatibility and budgets

Validated incident and synthetic snapshot contents are saved as bounded data,
not paths requiring the original files on resume. Model, instructions, request
policy, graph contract and dependency/source fingerprints are recorded. Resume
recreates clients using current credentials but retains the saved model/settings.
It rejects incompatible runtime fingerprints without changing saved state.
Inspect remains available for incompatible records; no migration is attempted.

Use the supported `PostgresSaver` with synchronous durability and committed
boundaries between graph nodes. Application tables are separate from the saver's
tables. They are not one atomic transaction: recovery explicitly reconciles them.
State contains data only; no clients, secrets, connections or arbitrary pickle.

Before each physical model/tool attempt, reserve a charged attempt durably under
exclusive ownership. Existing limits remain three model requests and two tool
attempts, including failed/denied tool lookups. A committed node output is stored
in the attempt ledger so graph replay can reuse it without issuing another call.
If a process dies between reservation and durable output, outcome is ambiguous:
the reservation remains charged and execution is refused as `recovery_required`.
Do not automatically resend or claim that the external call did/did not happen.
The user can inspect and start a separate investigation with a new ID.

A crash before reservation or after durable output can recover from the latest
checkpoint. Deterministic node updates may replay. Stable operation keys and
reducer semantics must prevent repeated evidence/history and double charging.
Failures with known errors are terminal. Recovery never resets used budgets.
Database/lock failure stops execution; loss of ownership must prevent further
attempts. Concurrent requests and pause/completion races have explicit tests.

## Completion checks

1. In separate processes, pause after a lookup, exit, inspect and resume with the
   same identity, original observations, fetched evidence, history and budgets.
2. Remove/change original input files and change ambient model configuration;
   resumed execution still uses the saved inputs/settings.
3. PostgreSQL restart and persistent Compose storage retain saved work.
4. Inject crashes before reservation, during an uncertain attempt, after durable
   output and before/after final checkpoint. Recover safely or refuse explicitly.
5. Concurrent/duplicate resumes do not execute in parallel or repeat completed
   work. Missing, completed, failed and incompatible IDs have tested behavior.
6. Persisted state, metadata and controlled diagnostics contain no configured
   credentials/client objects. Real input/output data is retained intentionally.
7. Existing regressions, offline PostgreSQL integration tests, lint, dependency
   compatibility, package build, Linux/Windows and container/Compose CI pass at
   the final feature head. Routine tests use a fake provider, never a live key.

## Local usage

Run these commands from the repository root with the installed environment active.
On Windows, activate `.venv/Scripts/Activate.ps1`; on macOS/Linux use
`source .venv/bin/activate`. Keep existing `.env` contents: if it predates this
feature, add the database settings from `.env.example` without overwriting your key.

```sh
docker compose --profile durable up -d --wait postgres
incidentclue db-init --env-file .env
incidentclue start data/synthetic/checkout-startup.json --health-file data/synthetic/checkout-health.json --logs-file data/synthetic/checkout-logs.json --paused --env-file .env
```

`--paused` needs no provider key. Copy the printed investigation UUID, then:

```sh
incidentclue inspect YOUR_UUID --env-file .env
incidentclue resume YOUR_UUID --pause-after execute --env-file .env
incidentclue inspect YOUR_UUID --env-file .env
incidentclue resume YOUR_UUID --env-file .env
```

The first resume may pause after a lookup. A model can choose no lookup, in which
case it completes without reaching `execute`. Resuming invokes the saved model;
changing `GROQ_MODEL` or editing the original input files does not replace saved
settings. A compatible runtime and current `GROQ_API_KEY` are required for new
model calls. From another terminal, `incidentclue pause YOUR_UUID --env-file .env`
requests a cooperative stop while execution is in progress.

Durable command stdout is a JSON inspection envelope, including diagnosis under
`state.diagnosis` when completed. The newly assigned ID is also printed to stderr
before execution so a failed run can be inspected. Exit codes are 0 for successful
operations (including pause/inspection), 1 for lifecycle/database/execution errors,
2 for input/configuration errors and 4 for an investigation already executing.
An `inspect` of a failed investigation is a successful read; check its `status`.
The one-shot commands keep their previous Diagnosis output and exit codes.

## Compose client and storage

The existing `incidentclue` Compose service remains the one-shot client. The
`durable` profile adds PostgreSQL and a database-connected CLI service:

```sh
docker compose build durable
docker compose run --rm durable db-init
docker compose run --rm durable start /inputs/checkout-startup.json --health-file /inputs/checkout-health.json --paused
docker compose run --rm durable inspect YOUR_UUID
docker compose run --rm durable resume YOUR_UUID --env-file /config/provider.env
```

The host CLI connects to localhost port 55432 by default; the Compose client
connects to `postgres:5432`. `INCIDENTCLUE_COMPOSE_DATABASE_URL` overrides the
container URI; update it as well as `INCIDENTCLUE_DATABASE_URL` if you change the
example database/user/password. URI-encode special characters in credentials.
Provider configuration is a read-only mount and is loaded only with `--env-file`.
The DB is published only on localhost. The documented password is for local
development. PostgreSQL is not deployed or exposed as a public service.

Checkpoints and app tables live in the project's `postgres-data` named volume;
one-shot JSON records remain in the separate `runs` volume. `docker compose down`
keeps these volumes. `docker compose down -v` destroys stored investigations and
records; do not use it to pause work. Credentials and connection objects are not
stored in graph state, but original inputs, conversations and model/tool results
are retained intentionally. There is no automatic retention cleanup or backup
service in this PR. Treat the local database as trusted application storage.

The `ic_schema`, `ic_investigations`, and `ic_attempts` tables are application-owned;
the checkpointer owns its checkpoint/migration tables. PostgreSQL session locks
require a direct connection or a session-preserving pool, not transaction pooling.
Inspection consists of several reads and may observe an advancing investigation;
it is not an atomic snapshot of all app and checkpoint tables while execution runs.

## Offline database tests

Use a separate Compose project/database for testing. Configure
`INCIDENTCLUE_TEST_DATABASE_URL` explicitly, then run:

```sh
python -m pytest backend/tests -m postgres -q
```

Tests retain uniquely identified test investigations in that selected database.
They use real PostgreSQL and fake SDK transports, including separate-process
restart, uncertain-call refusal, cached-output replay and concurrent resume/pause.
Crash cases include cached selection/final model output, interruption inside a
tool, and a known failed attempt whose application-status update was lost.
Without that variable, database tests skip and the existing offline suite runs.

## References

- [LangGraph checkpointers and synchronous durability](https://docs.langchain.com/oss/python/langgraph/checkpointers)
- [PostgreSQL checkpointer package](https://pypi.org/project/langgraph-checkpoint-postgres/)
