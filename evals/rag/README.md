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

Review defaults to development cases; held-out review requires `--split held_out`.
It checks frozen input hashes and reports missed abstentions, answerable cases
that abstained, citation membership and missing-evidence presence. Execution errors
and unattempted cases stay unassessed. Labels are read only by the evaluator.

Each report is reviewed separately; reruns do not replace earlier failures.
Automatic checks cannot establish causal support or the usefulness of checks.
Passing them still requires manual semantic review; fake responses establish no
model quality. The command makes no provider calls and refuses output overwrite.
