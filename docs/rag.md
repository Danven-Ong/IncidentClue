# Retrieval contract (implementation in progress)

The first increment supplies synthetic fixtures and an evaluation runner.
Runtime retrieval, database setup and source citations are not available yet.

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
