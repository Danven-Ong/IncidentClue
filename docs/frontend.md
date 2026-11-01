# Local browser client

The frontend is a client of the [HTTP API](http-api.md). The CLI remains supported
over the same engine and saved investigations. Open the application at incident
entry, create paused work, then explicitly resume it. Reopen known UUIDs using
`#/investigations/UUID`; refreshing that URL never starts execution.

## Boundaries

There is no server investigation list. The client has no recent-history store,
saved drafts, automatic execution/retry, background jobs, streaming, retrieval,
authentication or public deployment. PostgreSQL, provider credentials, budgets,
locking and recovery belong to the backend. No schema/graph changes are required.

Unsubmitted form input stays in memory when opening a UUID and returning to
incident entry. Successful creation clears it. Reloading or closing the page
discards it; no incident content is written to browser storage.

React/TypeScript and plain CSS live in `frontend/`. Native controls, a dark
charcoal theme, system typography and a minimal open-lens logo serve the local
investigation workflow. Evidence and model output are rendered as text. A saved
hypothesis is not a confirmed cause. Empty health evidence is unavailable data,
not proof of health. Suggested checks are text, never executable actions.

## Requests and observation

The browser calls relative `/api` URLs. Vite proxies those paths to loopback
Uvicorn in development. The built client uses an Nginx static/reverse-proxy
container with the same prefix mapping. Both strip `/api` before forwarding;
CORS remains disabled. Host bindings enforce local-only access. The frontend
build context contains only frontend inputs, never the backend environment file.

Create saves a UUID and paused checkpoint at zero attempts. A separate Resume
request executes synchronously in a backend worker thread. Inspection and Pause
use independent requests and remain available while Resume is outstanding.
Poll inspection about every two seconds without overlapping GETs while execution
or a local resume is active. Stop when settled, hidden/offline, navigating, or
after a read failure; inspect on return or manual refresh. Sequential inspection
is not an atomic snapshot. Counts come from the attempt ledger, not graph visits.

Responses for an obsolete UUID or request are discarded. Mutation responses are
followed by a fresh inspection rather than replacing a newer observation with an
older envelope. A failed observation retains the last data with a stale label.
Cooperative pause may still observe running, and completion may win.

Client disconnect, navigation, an aborted fetch or proxy timeout does not imply
cancellation or rollback. A lost mutation response has unknown outcome. Inspect
the known UUID before explicitly resuming again. Never automatically retry POSTs,
resume on load, or claim exactly-once execution. A lost creation response may
leave an unknown UUID; retrying can create a duplicate. The form remains available
with that warning. No client-side deduplication is promised.

The client checks JSON syntax, UUID shape and the 100,000-byte UTF-8 serialized
request limit. The server remains authoritative for schema and field limits.
Malformed success envelopes are protocol failures; partial `state` dictionaries
remain inspectable through known-field rendering and JSON details.

## Verification

Frontend tests cover protocol validation, form errors, lost responses, independent
pause, contention, stale reads and navigation. Browser tests use fake responses
for deterministic transport failures and a real FastAPI/PostgreSQL fixture with
fake provider calls for lifecycle integration. Routine checks never call a live
provider. Existing CLI, package, PostgreSQL recovery and container checks remain
required. Final review and explicit user approval precede merging.
