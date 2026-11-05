# Retrieval evaluation

## Support assessment contract and review flags

New generation requires a bounded support record with exact incident excerpts,
short model explanations and acknowledged blocking gaps. See
[the support contract](../../docs/rag.md#inspectable-diagnosis-support).
Scripted smoke responses include this record to test plumbing, not reasoning.

New paired review output uses schema_version2 and generation_contract_version2:

| Field | Meaning |
| --- | --- |
| support_record_available | A support record was saved; false means unavailable, not verified |
| support_internal_consistency_valid | The saved diagnosis agrees with its declared support/gaps; null when unavailable |
| support_excerpt_membership_valid | Excerpts match their actual cited observation text; null when unassessed |
| semantic_support_verified | Remains null for automatic review, even when the other checks pass |
| generation_diagnosis_contract_valid | Validates original content for a request that required the current support contract; null for older/unavailable requests |

Saved-result review uses the legacy-compatible path, rather than pretending old
outputs were asked to supply support. Rejected-generation diagnostics identify
generation_contract_version2 when checking the current generation shape; that
does not reconstruct a historical provider request or establish its rejection
cause. Exact excerpts and declared consistency do not prove semantic support.

Original traces, protocols and generated reviews remain unchanged. Before updating
scripted smoke fixtures/capture helpers, their prior source snapshots were retained
outside app Git in support-assessment-prior-harness-20261104. The original approved
investigator-regression protocol remains unchanged and now refuses runtime/helper
drift. Tests use a temporary offline-only protocol; they do not revise that live
approval or authorize a rerun. No new live support-format results exist.
The three historical paired runners now refuse live execution even with their old
protocol hashes, before connection or credential access. Fake smoke remains
available for integration checks. Prepare a new frozen protocol for any later
separately approved live assessment; the historical commands below describe the
original experiments and are not current execution authorization.

## Retained diagnostic results

Six selected synthetic development cases were attempted with and without RAG
across three separately approved batches with different pacing/stop policies.
All 12 distinct arms were attempted: nine accepted outputs and three execution
failures. These are not nine correct answers or a statistical accuracy estimate.

| Case | Without RAG: execution | With RAG: execution | Assistant semantic review |
| --- | --- | --- | --- |
| 01 | Accepted | Accepted | Both supported; similar useful check |
| 02 | Rate limit | Accepted | RAG bounded DNS cause supported; pod-environment assumption caveat; comparison incomplete |
| 08 | Accepted | Accepted | Both make unsupported causal claims; required abstention fails |
| 10 | Output rejected | Output rejected | No accepted diagnosis; semantic comparison unassessed |
| 11 | Accepted | Accepted | Both support bounded disk-full cause; no guidance returned |
| 12 | Accepted | Accepted | Both appropriately abstain; no guidance returned |

Case10/no-RAG rejected-content metadata describes valid JSON missing source_ids.
Its historical diagnosis_contract_valid flag checks the legacy contract only.
Case10/RAG instead describes invalid JSON, 64 characters and unknown truncation;
neither the same cause nor token exhaustion is established. All failures remain.
Reviews are assistant judgments, not independent human ground truth. RAG benefit
is unproven; failed held-out retrieval remains a separate failed result.

These paired results predate the subsequent strict-response validation correction
and revised diagnosis decision instructions. Do not attribute those retained
answers to the revision or retroactively pass a failed case.

### Subsequent no-RAG investigator regression: failed

One separately approved [regression protocol](investigator-regression-protocol.v1.json)
used [investigator_regression.py](investigator_regression.py), without a database,
embeddings or retrieval, on existing development cases01/08/10/11/12. It is a new
regression of inspected cases, not continuation of an old failed batch, an unseen
benchmark or a paired RAG comparison. The protocol pins source/runtime/input hashes.

All five executions succeeded. Assistant semantic review judged cases01/11's
bounded causes supported and cases10/12's abstentions appropriate. Case08 again
asserted an unsupported cause: it claimed DNS failures and subsequent connection
refusals caused the missing request mapping, which the observation does not
establish. The actual request contained the revised instructions. The central
abstention defect remains; overall regression quality failed.

The batch used15model requests/five tool attempts/10,240reserved completion tokens,
with15verified waits of at least65seconds and no retries, repairs or execution
failures. Known usage was9,007prompt/2,467completion tokens across all15requests.
Case01's Docker/Kubernetes check suggestions need applicability confirmation;
case11 offered no next checks. These caveats remain. Four bounded assistant-review
passes are not a statistical accuracy estimate or independent human validation.
No stronger output-reliability claim follows from one successful case10 result.

Evidence outside app Git: investigator-regression-live-20261104-a.jsonl,
investigator-regression-live-review-20261104-a.json/.md and
investigator-regression-live-semantic-review-20261104-a.json under mentoring/IncidentClue.
Do not combine its five executions with the original12distinct paired arms.
Original failures/held-out retrieval verdict remain unchanged; RAG benefit is
unproven. Approval covered this batch only. No further calls, automatic retries,
tuning loop or commit are authorized by these usage examples.

## Paired diagnostic evaluation

`paired.py` uses the existing graph and frozen v3 lookup through evaluator-owned
dependency injection, leaving normal runtime artifact acceptance unchanged.
Cases01/02/08/10/11/12 cover observed configuration/name-resolution mechanisms,
uncorrelated failures, deployment proximity, an observed no-runbook mechanism
and unspecified upload failures. All are development cases: no unseen accuracy
claim. The failed held-out result remains failed.

Read [paired-protocol.v1.json](paired-protocol.v1.json) before running. Both arms
receive identical input/snapshot availability; only the RAG arm is offered lookup.
Runbook calls are model-selected, once at most, sharing two total tool attempts.
No forced retrieval, extra model turn, canned diagnosis or annotation enters the
live graph. Fake mode deliberately returns smoke-only abstentions, never quality
judgments. Arm order alternates by case, with no statistical inference from this
small single pass. Graph state is ephemeral; this harness does not test durable
resume (covered by separate lifecycle tests).

Use the same isolated explicit loopback PG17/vector0.8.1 test database and cached
Windows artifacts/packages as earlier offline evaluators. Fake mode blocks external
Python sockets. This command performs real local embeddings/SQL with fake generation:

```bash
python evals/rag/paired.py --model-dir /path/to/verified-model --output /path/outside/repository/paired-fake.jsonl
python evals/rag/paired_review.py /path/outside/repository/paired-fake.jsonl --output /path/outside/repository/paired-fake-review.json
```

Review writes JSON and a Markdown worksheet, both exclusive and outside app Git.
It checks decision/citation structure, observation/source separation, per-run
budgets, proposed expected statuses and pairing. It includes scope references and
exact outputs/observations/excerpts for manual review. Semantic fields and overall
quality stay null until a named reviewer assesses every causal claim, document
claim, useful check and regression. Review all answer fields, including implicit
causes in next checks. Different check wording alone is not useful added value.
Unknown usage remains unknown; known token totals are partial when usage is absent.

Live execution is separately approval-gated, not authorized by these examples.
After approval, add `--live --approve-protocol SHA256` matching the exact protocol,
optionally `--env-file /path/to/.env`; fake mode never reads provider credentials.
Fixed model: `openai/gpt-oss-20b`. Limits:12investigations,36model requests,
24tool attempts,24,576reserved completion tokens;20second request timeout,900second
batch start deadline. An in-flight call may finish after the deadline by its timeout.
Stop at first provider/contract/retrieval/budget error or rate limit, preserving
remaining pair IDs. No retry, repair, model fallback or automatic continuation.
Raw usage may not provide a complete cost; the protocol makes no monetary estimate.
No output overwrite, app volume/image changes, downloads or runtime promotion.

Inputs are separate from evaluator-only labels. The corpus allowlist is
[`data/runbooks/manifest.json`](../../data/runbooks/manifest.json).
See the [rubric](rubric.md) before running or interpreting results.

With backend development dependencies installed, from the repository root:

```bash
python evals/rag/suite.py validate
python evals/rag/suite.py baseline --output /path/outside/repository/fake-baseline.jsonl
```

Baseline defaults to a local fake provider. It measures no diagnosis quality.
Results include input/fixture/runtime hashes, actual diagnosis, tool activity,
captured requests/responses, model attempts and observed duration. Output files
cannot be overwritten. Usage is recorded when supplied by the provider; missing
usage is unknown. Durable failure accounting still requires ledger-level tests.

Live execution additionally requires `--live`, an agreed `--model`, and an explicit
case split/count (`--split development --limit 12`). Credentials come from
`GROQ_API_KEY` or an explicitly supplied `--env-file`; they are never output.
Use this only for a separately agreed evaluation. No evaluations run on startup.
The runner stops the batch on its first provider rate limit without retries.
Known provider failures are classified by HTTP status and allowlisted error codes;
free-text error messages and headers are excluded. Numeric `Retry-After` hints
are recorded without automatically waiting or retrying.
Every SDK call has an `attempts` entry shared across SDK option copies, with its
phase, response/exception status, duration and observed usage (`null` when unknown).
`response_received` means the SDK returned, not that diagnosis validation passed.
Successful calls refer to a zero-based `response_index`; exceptions have no response
index or fabricated response. `responses` and `provider_usage` retain their existing
response-only layout, so their lengths can be smaller than `model_attempts`.

For exceptions, `rejected_generation` inspects `failed_generation` only when the
SDK exposes it as text of at most 16,384 characters. It records JSON parse status,
top-level type, types under fixed known field names, unknown-field count and (for
diagnosis) separate `legacy_diagnosis_contract_valid` and
`generation_diagnosis_contract_valid` results. The former allows omitted
`source_ids`; the latter requires it. Historical `diagnosis_contract_valid`
checked the legacy contract only and does not prove strict generation compliance.
Existing raw traces and reviews retain their original fields and outcomes.
Arbitrary keys, values, excerpts, decoder and
validation messages are excluded. Oversize/absent/non-text content and parser
limits are explicit. Contract validity does not establish citation membership,
semantic support or why the provider rejected a generation. Truncation remains
`unknown`: character counts, JSON syntax and ending braces cannot prove it.
These diagnostics cannot reconstruct content or usage missing from older reports.

Failed evaluation rows also include `failure_diagnostic`, a data-minimized summary
with a fixed origin (`provider`, `local_validation`, or `execution`), phase and
category. A locally invalid diagnosis may include up to three issue records with
schema paths, allowlisted validator types and static rule codes. Provider rejection
classification describes the returned provider code; it does not identify the
provider's internal rejection reason. `rejected_generation.generation_contract_issues`
reports only local validation findings for captured content. These fields contain
no generated values, excerpts, free-text error messages, or headers. The surrounding
evaluation JSONL still preserves its existing requests and responses, which can
include prompt, evidence and model text; treat the complete trace as sensitive.

Paired review now reports `generation_diagnosis_contract_valid` separately from
saved-result structure/citation checks, using original returned diagnosis content.
It is `null` when no uniquely identified returned generation is available,
including provider exceptions. Saved results can contain compatibility defaults
and cannot prove the original response supplied every required field. Neither
contract flag establishes normal completion, provider acceptance or semantic support.

Use `--case-ids case-19 case-20` to select specific cases within the chosen split;
this allows unattempted cases to run without implicitly replaying earlier ones.

The fixture validator checks hashes, source bounds, case partitions and canaries.
The default validator does not load the embedding tokenizer; its 200-token gate
must be verified separately against the pinned artifact before ingestion.
The optional runtime now implements ingestion, retrieval and separate source
citations; see [RAG setup and contracts](../../docs/rag.md).

## Offline development retrieval comparison

After installing the optional candidate dependencies and preparing verified local
artifacts, configure `INCIDENTCLUE_RAG_TEST_DATABASE_URL` to an **isolated loopback
PG17/vector0.8.1 database**, with an explicit host, port and database name. This
command explicitly creates retrieval schema/corpus records in that test database;
never point it at saved application data. It downloads nothing and requires no
Groq key:

```bash
python evals/rag/retrieval.py --model-dir /path/to/verified-model --output /path/outside/repository/retrieval-development.jsonl
```

This runner is development-only, with no live-generation or held-out option. It
uses the runtime query builder on each initial incident's summary/observations;
unfetched health/log snapshots are not added. Query generation and retrieval finish
before evaluator annotations are loaded for scoring. Frozen held-out files are
hashed for integrity but their inputs are not interpreted or scored. External
Python socket connections/DNS are blocked; libpq parameters are separately bound
to the explicit loopback database. Output is exclusive, flushed per row, and
partial failures are retained without raw connection/credential diagnostics.

The fixed grid is 0.20–0.70 in 0.05 increments plus a diagnostic-only 2.0. Each
configuration is ingested under its own versioned corpus ID; repeated passage
and query vectors are cached, while all searches execute through PostgreSQL and
the actual result bounds. Twelve cases produce 144 vector and 12 lexical searches,
with one real 36-passage embedding batch and 12 distinct query embeddings.
Timing includes caching and must not be presented as a latency comparison.

Metrics follow the frozen rubric: Precision@3 divides by three including empty
slots; recall/MRR/nDCG exclude the two empty-relevance cases and report that
denominator separately. Gains are `2^relevance - 1`. Unlisted passages keep grade 0;
annotation gaps are reported without relabeling. No-match false positives and
service/version violations have separate raw counts.

Selection requires at least 5/6 answerable hits, 0/2 no-guidance false matches,
zero scope/version violations, and vector MRR at least the lexical baseline.
Among eligible configurations, prefer nDCG, then Precision@3, then the lower
cutoff. The diagnostic upper bound cannot be selected. If none qualifies, record
`selected_cutoff: null`; do not relax the gates or promote a runtime setting.

The first measured decision is pinned in
[`calibration-development.v1.json`](calibration-development.v1.json), including
fixture/artifact/runner/report hashes and full aggregate results. **No candidate
qualified.** This is an inspected synthetic development result under
assistant-curated labels, not independent relevance truth, unseen performance,
tool-selection quality or improved LLM judgment. See the
[measured findings](../../docs/rag.md#first-offline-development-result) before
proposing a versioned baseline/query change. Live comparisons remain separately
budget-approved, and original strict diagnosis-quality gates remain unmet.

## Versioned representation and lexical comparison

The bounded second retrieval experiment is defined by
[`representation-protocol.v2.json`](representation-protocol.v2.json), with results
in [`calibration-development.v2.json`](calibration-development.v2.json). It adds
two evaluator-only candidates: OR over PostgreSQL English query lexemes, and
document-title-plus-passage embedding inputs. Physical model files, queries,
returned excerpts, corpus/labels and runtime policy stay unchanged.

```bash
python evals/rag/representation.py --model-dir /path/to/verified-model --baseline-report /path/to/archived-v1-raw.jsonl --output /path/outside/repository/representation-development.jsonl
```

Use the same explicit isolated loopback database configuration described above.
The archived baseline report must match the SHA256 pinned by v1; a freshly rerun
report with different timing bytes is not interchangeable. Keep the archived raw
evidence. The runner stops on a baseline input/query, source rank/ID or distance
mismatch (distance tolerance 0.000001). All 156 control searches reproduced in the
measured run. Both reports are exclusive and previous results remain unchanged.

The fixed run executes 312 searches: 288 vector searches for the body/title
representations over the same cutoff grid, plus 12 original AND and 12 OR searches.
It computes 72 real passage vectors in two batches and shares 12 query embeddings.
Title inputs include only the validated document title, a newline and the original
passage, with a 2,000-byte/200-token limit. Metadata/excerpts/source locations do
not change. The experimental artifact identity changes, so the current runtime
rejects that candidate corpus rather than silently using an unsupported policy.

OR changes only query Boolean matching: use distinct PostgreSQL English lexemes,
preserve passage `tsvector` terms and default `ts_rank_cd`, and retain the same
corpus/service/status filters, deterministic ties and byte/result bounds. Inputs
are SQL parameters; the database contract test covers long queries, stopwords and
SQL-like punctuation. There is no reranker, hybrid fusion or new service.

Selection retains the coverage, no-guidance and scope gates, now comparing vector
MRR against the improved OR lexical baseline. **No candidate qualifies.** OR hits
6/6 answerable cases but falsely matches both no-guidance cases. Title-prefixed
vectors at 0.30 get 5/6 hits with zero such false matches, yet MRR 0.60 is below OR
0.6833. Title MRR tops out at 0.65. Do not replace a failed MRR gate with the more
favorable recall/nDCG result. No runtime promotion or held-out scoring occurred.

## Bounded runbook expansion comparison (v3)

The [v3 protocol](runbook-protocol.v3.json) defines one development-only pass:
24 candidate retrievals at cutoffs 0.25/0.30, 24 title-vector controls and 12 OR
controls. It reuses the physical model, v2 title embedding policy, runtime query
builder, immutable corpus and frozen annotations. Documents are ordered by their
nearest scoped/current passage, then expanded to exact Mechanism/Checks sections
under the original top-three and byte bounds. Siblings retain their own distance;
their document's seed distance, rather than each sibling, passes the cutoff.

```bash
python evals/rag/runbook.py --model-dir /path/to/verified-model --baseline-report /path/to/archived-v2-raw.jsonl --output /path/outside/repository/runbook-development.jsonl
```

This writes schema/corpora only to the same explicitly isolated loopback test DB
described above. All existing socket/libpq guards apply. The v2 archive must match
the hash in its decision snapshot; control query/input/source metadata/ranks and
distances (1e-6 tolerance) must reproduce. No annotation scoring begins until all
retrievals finish. Output is exclusive and failures are retained. This command is
documentation for reproducibility, not permission for another development loop.

The [v3 decision](calibration-development.v3.json) selects 0.30 under the unchanged
coverage/no-guidance/scope/MRR gates: 6/6 answerable hits, 0/2 false matches,
MRR 0.933333 against OR 0.683333, recall 0.95, scope violations zero. The 0.25
candidate misses the coverage gate (4/6). All 36 controls reproduced; 60 retrieval
operations, 72 passage vectors, 12 query vectors, no downloads/provider calls or
held-out scoring. Repeat vectors are cached, so timing is not a latency benchmark.

This output policy deliberately favors actionable sections; improved rank metrics
alone cannot establish diagnosis quality. Synthetic development cases have been
repeatedly inspected. Preserve original labels/reports and v1/v2 failed decisions.
No runtime promotion is implemented, even though v3 meets development criteria.
Freeze a separate confirmation protocol before any held-out assessment; live
evaluation requires separate bounded approval. Do not keep tuning this split.

## Frozen held-out confirmation

The [confirmation protocol](confirmation-protocol.v1.json) fixes the selected v3
policy at 0.30. It replays 24 archived development controls, then runs the candidate,
original lexical AND and lexical OR once on each of twelve held-out cases. It
verifies frozen v3/helper/report hashes before work and opens held-out inputs only
after controls reproduce. All scoring annotations are loaded after retrieval.

```bash
python evals/rag/confirmation.py --model-dir /path/to/verified-model --development-report /path/to/archived-v3-raw.jsonl --output /path/outside/repository/confirmation-held-out.jsonl
```

Same isolated loopback database and external-socket/libpq guards apply. This
command documents the completed pass, not authorization to rerun inspected cases.
Output is exclusive and partial failures remain. The measured pass executed
60 operations, 72 passage vectors and 24 query vectors with zero downloads or
provider calls. [Confirmation failed](confirmation-held-out.v1.json): candidate
6/6 answerable hits, 1/2 no-guidance false matches, MRR 0.833333 below OR 0.85,
recall 0.85, zero scope/version violations. Runtime promotion and the conditional
live-evaluation path stopped. Do not substitute recall for either failed gate or
change original labels. Any further tuning requires a fresh held-out set.
Platform success and diagnosis quality remain separate; Linux checks are deferred
and any live generation evaluation needs separately bounded approval.

## Review saved results offline

```bash
python evals/rag/review.py /path/to/baseline.jsonl --output /path/outside/repository/review.json
```

Development review defaults to `--contract decision-v2`. Its evaluator-only
[`decision-expectations-development.v1.json`](decision-expectations-development.v1.json)
separates expected diagnosis status from retrieval category: seven expected
hypotheses and five expected abstentions across the full development split.
The disk-full no-guidance case still expects a bounded explanation; no-guidance
does not itself require abstention. These annotations are assistant proposals,
not independently reviewed ground truth. Claim-scope/gap descriptions guide manual
review; the automatic reviewer checks expected status, not their semantic content.

[`decision-contract.v2.json`](decision-contract.v2.json) pins the annotation hash,
version, split, provenance and original fixture version. Reviews record the
selected contract, its combined hash (including reviewer/manifest/annotation
hashes), and annotation provenance. Reject mismatched hashes, versions, incomplete
or duplicate case sets, inconsistent required abstentions and invalid scope fields.
Frozen inputs, original labels/rubric and `frozen.json` are unchanged. Keep old
contract files and reports when introducing later versions.

`expected_status_counts` gives planned, assessed, matched, mismatched and
unassessed counts separately for both expected statuses, over the cases selected
in that report. `unexpected_abstentions` includes all expected-positive cases,
including diagnostic no-guidance cases; `unexpected_hypotheses` counts failures to
abstain. Execution errors and unattempted cases never count as status mismatches.
The existing `answerable_cases_abstained` retains its category-specific meaning
and covers only the six cases labelled `answerable`.

Use `--contract legacy-v1` to retain the previous checks without the new annotation
targets; new target counters are `null` in that mode. A legacy review made by this
version records this reviewer's hash; exact reproduction of older output hashes
requires the original reviewer revision. Existing saved reviews are never rewritten.
Held-out decision annotations have not been reviewed, so held-out review requires
explicitly selecting the legacy contract:

```bash
python evals/rag/review.py /path/to/held-out.jsonl --split held_out --contract legacy-v1 --output /path/outside/repository/held-out-review.json
```

It checks frozen input hashes and reports missed abstentions, answerable cases
that abstained, citation membership and missing-evidence presence. Execution errors
and unattempted cases stay unassessed. Labels are read only by the evaluator.

Each report is reviewed separately; reruns do not replace earlier failures.
Automatic checks cannot establish causal support or the usefulness of checks.
Passing them still requires manual semantic review; fake responses establish no
model quality. `quality_pass` stays `null` after automatic success and `false`
after failure under either contract. A matching status is not a supported diagnosis.
The command makes no provider calls and refuses output overwrite. Neither
generation nor retrieval reads the new evaluator-only annotation/manifest files.

## Paced diagnostic protocol v2 (live approval pending)

`paired_paced.py` is a separate frozen snapshot of the v1 harness with only the
case scope, batch limits and pre-request pacing changed. The original runner,
reviewer, protocol and stopped live trace remain unchanged. It selects only
case-08, case-10, case-11 and case-12: eight investigations, 24 model requests,
16 tool attempts and 16,384 reserved completion tokens maximum. No retries,
repair, fallback or automatic continuation; stop at the first error or 429.

Wait 65 seconds before every live provider request, including the first; wait in
five-second chunks, count waits against a 2,400-second batch start deadline and
check the deadline again before reserving/sending. Request timeout stays 20s.
Fake mode never sleeps or reads credentials. Both arms use identical pacing.

The public Groq rate-limit table checked 4 November 2026 lists this model at
30 RPM / 8,000 TPM / 1,000 RPD / 200,000 TPD; actual organization limits and daily
balance are unverified. Fixed spacing reduces burst pressure but does not promise
avoiding a 429, especially with other organization traffic or daily exhaustion.
Source: https://console.groq.com/docs/rate-limits

After separate approval of the exact `paired-protocol.v2.json` SHA256, use the same
cached Windows Python3.12 dependencies, model directory, isolated loopback/tmpfs
PostgreSQL and exclusive external output path as v1. Invoke `paired_paced.py`
with `--live --approve-protocol HASH`; offline review is `paired_paced_review.py`.
No new live batch is authorized by this documentation. The expanded deadline is
part of the new approval scope. Preserve failed held-out confirmation and prior
partial results; these development diagnostics cannot establish release acceptance.

## Remaining-arm protocol v3 (live approval pending)

`paired_remaining.py` / `paired-protocol.v3.json` select only the five v2-unstarted
arms in order: case10/noRAG, case11/noRAG, case11/RAG, case12/RAG, case12/noRAG.
No previous attempted arm is repeated. Case10/RAG remains a failed comparison arm.
Caps:5investigations/15modelrequests/10toolattempts/10240reservedcompletiontokens;
65s before every request including first,20s timeout,1500s batch-start deadline.
Same cached Windows embeddings, frozen retrieval, real graph, isolated tmpfs DB.

After an exact DiagnosisError in diagnosis content/validation, or HTTP400 with
provider code json_validate_failed in diagnosis, retain the failed investigation
and advance to the next scheduled arm. No retry or repair. Selection errors,
429/auth/transport/timeout/context/storage/retrieval/budget/deadline and unknown
errors stop the batch. Summary distinguishes attempted, successful and failed
investigations, schedule completion and all-success. Finishing the schedule does
not make failed answers pass; exit status remains unsuccessful if any arm fails.

Use `paired_remaining_review.py` for the same unscored semantic-review worksheet.
V1/v2 protocols, runners and evidence remain unchanged. New exact-protocol user
approval is required before live v3 execution. No tuning or runtime promotion.
