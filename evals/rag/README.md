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
Use `--case-ids case-19 case-20` to select specific cases within the chosen split;
this allows unattempted cases to run without implicitly replaying earlier ones.

The fixture validator checks hashes, source bounds, case partitions and canaries.
The default validator does not load the embedding tokenizer; its 200-token gate
must be verified separately against the pinned artifact before ingestion.
This foundation does not implement ingestion, retrieval or source citations.
