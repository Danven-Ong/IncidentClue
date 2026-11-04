# Retrieval contract (implementation in progress)

## Inspectable diagnosis support

New generations now require `support_assessment` in the same diagnosis request:
`evidence_links` contain an incident `evidence_id`, exact `excerpt` and a short
model-written `relation`; `blocking_gaps` lists acknowledged facts missing from
the incident-level conclusion. Hypotheses need support links covering every
evidence citation and no declared blocking gaps. Abstention needs at least one
blocking gap. An unknown deeper origin need not block a directly observed mechanism.

Reject foreign IDs, nonmatching/blank excerpts, duplicate links, missing fields
and internal inconsistencies without repairing or rewriting the answer. Each
list is capped at three entries, excerpts/relations/gaps at240characters, and
the serialized support object at2,000UTF-8bytes. Existing request/tool/input/token
limits stay unchanged; the extra output can increase generation pressure within
the existing1,024-token diagnosis cap. Provider compatibility and semantic behavior
of this new format have not been live-evaluated.

Support is saved inside Diagnosis and exposed through CLI/API inspection. Browser
inspection shows model explanations, reported gaps and expandable full original
observations; content is rendered as text. Legacy results with no support load as
`null`/unavailable, never reconstructed or treated as verified. The existing
checkpoint class allowlist is unchanged; unfinished old runs remain subject to
source-fingerprint incompatibility and are not reset or migrated.

These checks establish quote provenance and declared consistency, **not causal
truth**. A model can quote case08 exactly and still give a false interpretation.
That counterexample is explicitly covered by offline tests. No semantic fix,
RAG benefit, new model request, judge layer or human approval workflow is claimed.
Earlier raw results and failures remain unchanged. The previous live protocol
now refuses changed runtime/helper hashes; its consumed approval cannot authorize
the new support format. Any later live evaluation needs a separately frozen scope.

## Current verdict and investigator regression

The retained paired diagnostic attempted all 12 distinct arms of six selected
development cases across three separately approved batches: nine accepted
outputs and three execution failures. Accepted output is not correctness.
Both case08 arms made unsupported causal claims; both case10 arms were rejected
by the provider. Cases11/12 behaved appropriately with no guidance returned.
RAG benefit remains unproven and held-out retrieval confirmation remains failed.
See the [qualified results summary](../evals/rag/README.md#retained-diagnostic-results).

After those runs, new-response validation was corrected to require source_ids
in both arms while retaining legacy saved-result compatibility. Diagnosis
instructions were subsequently revised to choose a bounded observed mechanism,
check its connection to the affected operation and abstain on unresolved causal
links. This instruction candidate is shared by direct and graph diagnosis.
It preserves supported hypotheses when a deeper origin is unknown; it adds no
judge, case-specific filter, repair, retry or extra model request.

The original paired answers predate the revision and retain their original
verdicts. A separately approved [no-RAG regression protocol](../evals/rag/investigator-regression-protocol.v1.json)
then ran five inspected development cases with the revised instructions. All five
produced accepted outputs; assistant semantic review found four appropriate
bounded outcomes and one failure. Case08 again asserted an unsupported cause
instead of abstaining, despite verified delivery of the revised prompt. The
instruction rewrite therefore did not resolve the central reasoning defect.

The regression used 15 model requests, five tool attempts and no retries. It
does not measure RAG benefit or generalization, and its successful case10 output
does not erase the original provider rejections. Keep this regression separate
from the original 12-arm paired diagnostic. Model, retrieval and execution caps
remain unchanged. No further batch or inspected held-out tuning is authorized
by this document. Full evidence remains outside application Git in the mentoring
records as investigator-regression-live-20261104-a.jsonl and its automatic and
assistant semantic reviews.

## Experimental paired diagnosis evaluation

An evaluator-only [paired protocol](../evals/rag/paired-protocol.v1.json) and
[harness](../evals/rag/paired.py) compare six development incidents with and
without runbooks through the existing investigation graph. This is a diagnostic
experiment following failed retrieval confirmation, not runtime promotion or a
new held-out benchmark. The evaluator supplies the frozen experimental lookup
through existing graph hooks; production corpus acceptance stays unchanged.

Each arm has the same model, input snapshots and three-model/two-tool limits.
Runbook lookup competes with evidence tools and is never forced. Reports retain
requests/responses, charged failures, actual observation sets, exact sources,
available usage and timings. Pair review flags differences in fetched observations
rather than assuming both arms saw the same final evidence. A generated worksheet
requires claim-by-claim observation support and separate document support; citation
membership and expected status alone do not establish correctness.

Default execution uses a scripted provider, which measures integration only.
Live generation needs separate bounded approval of the exact protocol and an
explicit live flag. One batch is capped at twelve investigations, 36 model calls,
24 tool attempts and 24,576 reserved completion tokens, with no retries and stop
on the first execution error. Normal application behavior, saved investigations
and the failed confirmation result are unchanged. Semantic quality is unassessed
until actual live outputs are reviewed; assistant review is not independent human
review. See [evaluation usage](../evals/rag/README.md#paired-diagnostic-evaluation).

## Held-out confirmation failed

On 4 November 2026, a separately frozen protocol tested the selected 0.30 policy
once on all 12 held-out cases, after reproducing 24 archived development controls.
It used unchanged v3 code, original corpus/labels/query policy, cached Windows CPU
embeddings and isolated PostgreSQL. No tuning or extra cutoffs were tested.

| Method | Answerable hits | No-guidance false matches | MRR@3 | Recall@3 |
| --- | --- | --- | --- | --- |
| Runbook expansion, 0.30 | 6/6 | 1/2 | 0.8333 | 0.85 |
| Lexical OR | 6/6 | 2/2 | 0.85 | 0.70 |
| Original lexical AND | 0/6 | 0/2 | 0.00 | 0.00 |

**Confirmation failed:** the candidate must return no guidance in both
no-guidance cases and have MRR at least lexical OR. Case23 received connectivity
guidance despite its no-guidance label; case22 returned no labeled relevant
guidance, and case15's first relevant passage ranked third. Scope/version
violations were zero, but higher recall cannot substitute for the failed gates.

The [frozen confirmation protocol](../evals/rag/confirmation-protocol.v1.json)
and [retained held-out result](../evals/rag/confirmation-held-out.v1.json) pin the
60-operation pass, source/runner/report hashes and full metrics. All 129 returned
excerpts/metadata/scope/byte bounds were audited. These cases are now inspected:
do not tune/relabel/retry them into a pass. Future tuning needs a fresh held-out
set. Runtime promotion stopped; live generation was not run. The development
success below remains a development result only. Original model limitations,
strict unmet diagnosis-quality gate and separate bounded live approval remain.

## Runbook expansion development result

On 4 November 2026, one predeclared evaluator-only experiment selected documents
by their nearest title-context passage and returned each document's original
Mechanism and Checks sections. It retained the three-source/3,000 excerpt-byte/
4,000 serialized-byte bounds, frozen labels and v2 lexical OR selection floor.

| Candidate / cutoff | Answerable hits | No-guidance false matches | MRR@3 |
| --- | --- | --- | --- |
| Runbook expansion, 0.25 | 4/6 | 0/2 | 0.70 |
| Runbook expansion, 0.30 | 6/6 | 0/2 | 0.9333 |
| Replayed lexical OR | 6/6 | 2/2 | 0.6833 |

The 0.30 candidate meets the unchanged **development** selection gates, with
Recall@3 0.95 and zero scope/version violations. All 36 archived title-vector/OR
controls reproduced. The complete pass executed 60 retrieval operations using
two real 36-passage batches and 12 cached query vectors. Exact excerpts and source
locations were audited; no generation, held-out scoring or downloads occurred.
Protocol and results are in [v3 protocol](../evals/rag/runbook-protocol.v3.json)
and [v3 development decision](../evals/rag/calibration-development.v3.json).

The cutoff selects the **document**, not every returned sibling passage. Sources
retain their own cosine distances, which may exceed the cutoff; document seed
IDs/distances are recorded separately. Expansion orders Mechanism before Checks,
so rank metrics reflect this new output policy as well as document matching.
Some third slots contain competing guidance. This small, repeatedly inspected
synthetic development result does not establish independent relevance, semantic
support, tool choice, improved diagnoses or generalization. V1/v2 failures remain
unchanged. No runtime policy was promoted; experimental title corpora remain
unsupported by current runtime pinning. A separately frozen confirmation protocol
is needed before any held-out assessment, and live evaluation still needs bounded
approval. Linux/Python 3.13 verification remains explicitly deferred.

## First offline development result

On 2 November 2026, the cached real CPU model and isolated PG17/vector0.8.1
database ran the current query policy against all 12 development cases. The
corpus, labels, runtime policy and application configuration were unchanged;
held-out inputs were not scored and no Groq calls were made.

| Method / cutoff | Labeled answerable hits | No-guidance false matches | MRR@3 |
| --- | --- | --- | --- |
| Current full-text baseline | 0/6 | 0/2 | 0.00 |
| Vector 0.25 | 4/6 | 0/2 | 0.55 |
| Vector 0.35 | 5/6 | 1/2 | 0.60 |
| Vector 0.40 | 5/6 | 2/2 | 0.60 |

MRR is averaged over the 10 cases with nonempty relevance labels. All methods
had zero service/superseded-version violations. Full results and the predeclared
selection policy are in the evaluator-only
[development calibration record](../evals/rag/calibration-development.v1.json).
**No cutoff met the selection criteria; none is recommended or promoted.**

The fifth labeled answerable hit needs distance about 0.346, while a no-guidance
case already matches at about 0.290. With this ranking and these labels, a global
distance cutoff alone cannot meet both targets. The existing lexical baseline
uses English `plainto_tsquery` over the whole incident query, requiring all terms;
it returned empty results on all 12 cases. That weak baseline does not establish
general vector superiority. A later baseline/query change needs a separately
versioned development comparison, rather than rewriting this result.

There is also a possible annotation-granularity gap: case04 ranks
`compatibility#symptoms` first, while frozen labels only credit that document's
mechanism/checks. Case10 similarly ranks `evidence#symptoms` without its labeled
guidance in the top three. Unlisted passages remain grade 0; these observations
are not retrospective score corrections. Symptom similarity can also be useless
or misleading, as the disk-full and unspecified-upload no-guidance cases show.

These are inspected synthetic fixtures with assistant-curated labels. The pass
evaluates initial-state retrieval only, with no model tool selection or diagnosis
generation. It does not show improved judgment or satisfy the original strict
unsupported-claim gate. Next review the lexical query and passage representation
as versioned development changes; held-out and live evaluation remain separate.

## Versioned representation comparison

The second bounded development pass tested an OR lexical query and a title-prefix
embedding representation. It reproduced all 156 prior control searches, then
compared the candidates under the same frozen annotations and cutoff grid.

| Candidate | Labeled answerable hits | No-guidance false matches | MRR@3 |
| --- | --- | --- | --- |
| Lexical OR | 6/6 | 2/2 | 0.6833 |
| Title-prefixed vectors, 0.30 | 5/6 | 0/2 | 0.60 |
| Title-prefixed vectors, 0.35 | 6/6 | 1/2 | 0.65 |

Title context improves the schema-mismatch guidance ranking and moves pool checks
to first place, but also moves the dependency-latency mechanism below a symptom
passage. Lexical OR is a more useful comparator than the original empty-result AND
baseline, while still returning unrelated guidance for no-guidance inputs.
Vector MRR remains below the OR baseline; neither candidate meets all criteria.
**Selection remains null and runtime behavior is unchanged.** Improved recall or
nDCG does not retrospectively satisfy the failed MRR/no-guidance gates.

The evaluator-only [v2 protocol](../evals/rag/representation-protocol.v2.json) and
[v2 decision](../evals/rag/calibration-development.v2.json) preserve the fixed
312-search budget, two real passage batches, 12 query embeddings and full results.
No labels, corpus excerpts, generation model, query policy or application config
were changed; no external calls or held-out scoring occurred. Experimental corpus
identities cannot be opted into the current runtime without a separate supported
policy implementation. Avoid an automatic experiment loop: review ranking goals
and annotation granularity before any further versioned retrieval change.

## Implementation status

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
