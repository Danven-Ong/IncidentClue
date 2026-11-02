# Retrieval contract (implementation in progress)

Synthetic fixtures and the no-RAG evaluation runner are available. The optional
`incidentclue.rag` module now implements corpus validation, local CPU embeddings,
explicit schema setup, immutable ingestion, exact cosine lookup and a PostgreSQL
full-text comparison. Optional durable graph integration now pins retrieval
settings, charges a lookup within the existing tool budget, and saves separate
source citations for CLI/API/browser inspection. Administrative operations do not
call Groq; integration tests use fake model responses. Live quality is unmeasured.

The seven storage tests pass against an isolated PostgreSQL 17/vector 0.8.1
container, using deterministic test vectors. No application extension image has
been adopted, no existing volume has been changed, and no retrieval relevance or
end-to-end improvement is claimed.

## Optional preparation and probes

Keep the normal application setup for non-RAG use. Optional candidate dependencies
are listed in `backend/requirements-rag.candidate.txt`; they have prior Windows/Linux
3.12/3.13 binary-resolution probes and a Windows/Python 3.12 CPU execution check.
Native Linux and Python 3.13 embedding execution remain unverified. The base
runtime lock and Docker image do not install the optional dependencies.

This candidate is provisional, not an adopted platform lock. In a separate test
environment, explicitly install that candidate and the built application
wheel before embedding/ingestion. Inspect the candidate before installing: this is a
separate dependency setup step, not a request-time operation. No command below
installs packages or downloads a model.

The packaged `incidentclue/rag/artifact.json` identifies the MIT-licensed
`Qdrant/bge-small-en-v1.5-onnx-Q` artifact at revision
`aa8f8b060edb00e03bfdd08813a2949946c8ba55`. Place its six listed files in a local
directory through an explicit acquisition step. Each file's byte length and SHA256
must match before FastEmbed loads it. The backend uses only CPU, one thread,
`local_files_only=True`, and the supplied directory. Missing/corrupt files or
different embedding package versions fail. There is no acquisition fallback.
The real local check loads all 36 passages and a query with sockets blocked;
this verifies artifact/API compatibility, not semantic quality.
Repeat it against installed optional dependencies with
`INCIDENTCLUE_RAG_MODEL_DIR` set to that directory and
`python -m pytest backend/tests/test_rag_embedding_local.py -q`.

From the repository root, using an installed wheel:

```sh
python -m incidentclue.rag validate --root . --max-distance 0.4
```

The cutoff here is an **experimental example**, not a recommendation or calibrated
threshold. It is mandatory, finite, between 0 and 2, and part of the corpus ID.
Changing it produces another immutable corpus version. Validation reads only
the allowlist and its documents; it needs no database, provider or embedding
package and does not check embedding token counts.

Use an isolated PostgreSQL **17** database with the **vector 0.8.1** extension
files installed in its server image. Set `INCIDENTCLUE_DATABASE_URL` in the process
environment; this module does not read `.env` or provider keys. Setup requires
administrative DDL privileges. A normal PostgreSQL image without the extension
fails clearly. Preserve saved volumes; prove retention on copied/isolated data
before any future image change.

```sh
python -m incidentclue.rag setup
python -m incidentclue.rag ingest --root . --max-distance 0.4 --model-dir /path/to/verified-model
python -m incidentclue.rag inspect --corpus CORPUS_SHA256
python -m incidentclue.rag lookup --corpus CORPUS_SHA256 --service billing --query "resolver failed before connecting" --mode vector --model-dir /path/to/verified-model
python -m incidentclue.rag lookup --corpus CORPUS_SHA256 --service billing --query "resolver failed before connecting" --mode lexical
```

Only `setup` creates the extension and `ic_rag` schema. Existing incompatible or
partial schemas are refused without upgrade. Ingestion locks competing publishers,
checks all passage token counts/vectors, and inserts the header and every passage
in one transaction. Repeating a published corpus skips document embedding and
inserts. Its local loader still verifies artifacts before this check. Old corpus
versions are retained; there is no destructive reset or mutable active pointer.
Inspection and lookup perform read-only preflight and never initialize storage.

Both lookup modes filter exact corpus, current documents, and exact service or
explicit global applicability before ranking. Cosine uses an exact scan with no
approximate index; lexical uses English `plainto_tsquery`/`ts_rank_cd` on passage
text, with no embeddings or distance cutoff. Stable passage IDs break ties under
the PostgreSQL C collation. The same source/context bounds apply to both.
Outputs distinguish eligible passages, matched passages, scope/threshold removal,
and context-bound exclusions. Lexical scores and cosine distances are different
quantities, neither a probability of incident causation.

These probes accept a bounded explicit query (at most 2,000 UTF-8 bytes, and 200
embedding tokens for vector mode). They do not perform an investigation or access
its attempt ledger. The integrated graph tool constructs its own query from saved
observations, validates the exact service, and reserves the existing tool budget
before local model loading, query embedding or SQL. Runbooks stay separate from
observations. The fingerprint now covers nested runtime code, the packaged
artifact and executed optional dependencies on retrieval-enabled runs.

## Opt-in durable investigation

Prepare and ingest explicitly, installing optional dependencies before creating
RAG investigations. For native CLI use, pass the exact corpus ID:

```sh
python -m incidentclue.cli start data/synthetic/checkout-startup.json --health-file data/synthetic/checkout-health.json --runbooks CORPUS_SHA256 --paused --env-file .env
```

For new investigations on a native API server, set `INCIDENTCLUE_RAG_CORPUS_ID`
in its process environment. Set `INCIDENTCLUE_RAG_MODEL_DIR` to the verified model
directory for lookup. HTTP creation payloads/routes are unchanged, with no browser
toggle. Default is disabled. One-shot `diagnose`/`investigate` remain non-RAG and
database-independent. Compose retains its original image/dependencies: native
optional setup does not imply the application container supports embeddings.

Creation reads the prepared corpus and saves corpus/artifact/config hashes plus
the query policy, with zero charged attempts and no embedding/provider calls.
Later configuration changes affect new investigations; existing runs retain their
saved pin. Missing configuration/storage is an error, never a fabricated no-match.

`get_runbook_context(service)` is offered only on opted-in investigations and can
execute once, competing with health/logs for the same two total tool attempts.
Lookup failures keep their charge and fail execution. A reservation with uncertain
output refuses replay; a committed output is reused after a crash even if its
graph checkpoint was not saved. There are still three model requests at most,
with the existing 512-selection/1024-diagnosis caps and reasoning policy.

Query policy `summary-observations-prefix-200tokens-v1` concatenates only the saved
summary and currently available observation contents in saved order. It takes a
UTF-8-safe 2,000-byte prefix, then shortens that prefix until the pinned tokenizer
counts at most 200 tokens. The saved tool result records policy, query hash, byte
count and truncation. IDs, labels, paths and retrieved text are not query inputs.
The complete bounded incident evidence still reaches diagnosis when the query
is shortened. This policy and the distance cutoff require development calibration.

Passages remain in `retrieved_sources`; they never become observations. Strict
generation requests require separate `source_ids`, validated only against returned
passages in the pinned corpus. Reject duplicate/conflicting/foreign sources and
evidence/source collisions. Hypotheses still need incident `evidence_ids`.
Membership checks cannot prove semantic support. Guidance may help judgment and
checks, but cannot create missing incident facts.

CLI JSON and HTTP inspection expose the pin, excerpts and citations. Browser
citations focus the exact saved excerpt in a separate Runbook guidance section;
version, hash, section/lines and repository-relative location remain readable.
HTML/URLs are displayed as text, never executed. Legacy saved diagnoses without
`source_ids` remain readable as an empty list. Inspection and completed resume do
not reload corpus/model files or require provider keys.

The fingerprint is now graph contract version 2. Changed code or optional
dependencies can make unfinished runs incompatible; preserve their data and
inspectability, refusing resume without reset/migration. Install optional
dependencies before creation rather than changing the environment mid-run.

Run offline contract checks with `python -m pytest backend/tests/test_rag_retrieval.py`.
The separate `backend/tests/test_rag_postgres.py` suite requires optional pgvector
dependencies and `INCIDENTCLUE_RAG_TEST_DATABASE_URL` pointing to an isolated
PG17/vector0.8.1 database. It explicitly creates retrieval tables and retains
test corpus records; never point it at saved work. Missing database configuration
skips those tests, which must be reported as unverified. They cover atomic
rollback, idempotency, version retention, service/supersession filters,
deterministic ties, thresholds, lexical no-match and incompatible setup.

No cutoff calibration, held-out quality claim or live model evaluation is part
of these preparation commands. Live evaluations require separate bounded approval.
The retained 20B development review is 10/12 planned outcomes and 4/5 required
abstentions, including one unsupported ambiguous cause and one connection failure.
It is assistant/manual review of inspected synthetic data; the original strict
zero-unsupported-claim gate remains unmet. Runbook guidance may improve judgment
and useful checks, but cannot supply missing incident observations.

## Corpus and lookup

Only paths in `data/runbooks/manifest.json` are ingestible. Heading sections retain
their exact line ranges; reject passages over 1,000 UTF-8 bytes or 200 embedding
tokens. Maximum: 12 documents and 48 passages. Store source title, service scope,
version/effective date, hash and current/superseded status. Superseded documents
remain stored but are excluded from new lookups.

Publish each complete corpus atomically under a content hash, including its
embedding artifact, chunking and retrieval configuration. Identical ingestion is
a no-op. Retain versions pinned by saved investigations.

`get_runbook_context(service)` validates the incident service. Python constructs
the query from saved summary/observations; the model cannot supply SQL, paths,
URLs or versions. Filter by pinned corpus and exact service or explicit `global`
scope before exact cosine ranking. Break ties by stable passage ID. Compare with
PostgreSQL full-text ranking over identical eligible passages. Freeze the distance
cutoff and query policy using development data before held-out evaluation.

Return up to three unique passages: at most 3,000 passage bytes and 4,000 bytes of
serialized source context including metadata. Incident input remains bounded at
12,000 bytes; combined incident/source context at 16,000 bytes. Scores indicate
relevance, not confidence in a diagnosis. No match is an ordinary successful empty
result; missing artifacts, configuration and storage are controlled failures.

## Saved and public data

Server configuration opts new investigations into retrieval; CLI adds an explicit
opt-in. Existing HTTP create payload and routes remain unchanged. Creation saves
`retrieval: null` when disabled, otherwise corpus/config/artifact hashes, with zero
attempts and no query embedding. No browser toggle is required.

State adds `retrieved_sources`, separate from observations. Each saved entry has
`id`, `corpus_id`, `document_id`, `document_version`, `document_sha256`, `title`,
`path`, `section`, `line_start`, `line_end`, `text`, `rank` and `distance`.
Source IDs use `source:<corpus-hash>:<passage-id>`; evidence/source collisions and
conflicting source IDs are rejected. Render saved excerpts and allowlisted local
locations as text/links, never arbitrary HTML or URLs. A current file must match
the saved hash to claim it is the same version; the saved excerpt is authoritative.

Diagnoses add `source_ids`. Old saved results omit it and are interpreted as an
empty list; the new strict generation schema requires an explicit list. Validate
each citation against returned sources in the pinned corpus. Hypotheses still
require at least one current-incident `evidence_id`. Membership validation does
not establish semantic support. Relevant guidance can accompany
`insufficient_evidence`; a document alone cannot establish a cause.

## Execution and compatibility

Keep direct SDK generation and the shared LangGraph. Retrieval can run at most
once, within the existing two tool/three model attempts. Reserve the tool attempt
before embedding/SQL; commit bounded output before continuation. Reuse committed
output after a crash. Uncertain attempts retain their charge and refuse replay.
API/CLI inspection and completed resume use saved excerpts with zero calls.

Add extension/schema setup explicitly, separate from health/inspection reads.
Retain PostgreSQL 17, saved volumes and base one-command startup without model
downloads or a corpus. Optional RAG preparation acquires artifacts explicitly.
Prove image/storage compatibility on isolated or copied data before adoption.
Runtime changes may refuse resume of incompatible unfinished investigations;
preserve their data and inspectability without reset or automatic migration.

The [evaluation suite](../evals/rag/README.md) defines quality gates separately
from fake-provider contract tests. This contract does not claim passing quality
results. History/list APIs, extra selectors, remediation and hosted services are
outside this feature.
