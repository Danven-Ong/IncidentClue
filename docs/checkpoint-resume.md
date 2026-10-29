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
  tables; repeated initialization is safe. Normal commands never silently migrate.
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

## References

- [LangGraph checkpointers and synchronous durability](https://docs.langchain.com/oss/python/langgraph/checkpointers)
- [PostgreSQL checkpointer package](https://pypi.org/project/langgraph-checkpoint-postgres/)
