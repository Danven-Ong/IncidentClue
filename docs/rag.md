# Retrieval contract (implementation in progress)

Synthetic fixtures and the no-RAG evaluation runner are available. The optional
`incidentclue.rag` module now implements corpus validation, local CPU embeddings,
explicit schema setup, immutable ingestion, exact cosine lookup and a PostgreSQL
full-text comparison. Investigation/graph integration and diagnosis source
citations remain pending. These administrative operations do not call Groq.

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
its attempt ledger. The future graph tool must construct its query from saved
observations in Python, validate the exact service, and reserve its existing tool
budget **before** embedding/SQL; this increment does not bypass or implement that
integration. Keep runbooks separate from observations throughout.
Before graph integration, extend the conservative runtime fingerprint to cover
this subpackage and its executed optional dependencies. The current investigation
fingerprint is unchanged because the investigation runtime does not import or
execute this administrative module.

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
