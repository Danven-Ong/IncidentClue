# Retrieval evaluation

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
diagnosis) local contract validity. Arbitrary keys, values, excerpts, decoder and
validation messages are excluded. Oversize/absent/non-text content and parser
limits are explicit. Contract validity does not establish citation membership,
semantic support or why the provider rejected a generation. Truncation remains
`unknown`: character counts, JSON syntax and ending braces cannot prove it.
These diagnostics cannot reconstruct content or usage missing from older reports.

Use `--case-ids case-19 case-20` to select specific cases within the chosen split;
this allows unattempted cases to run without implicitly replaying earlier ones.

The fixture validator checks hashes, source bounds, case partitions and canaries.
The default validator does not load the embedding tokenizer; its 200-token gate
must be verified separately against the pinned artifact before ingestion.
This foundation does not implement ingestion, retrieval or source citations.

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
