# RAG evaluation rules

All documents and inputs are curated synthetic fixtures. Development and held-out
cases use different scenario families. Labels are evaluator-only; neither runtime
query construction nor ingestion may read this directory.

## Relevance

Score each returned passage: 2 directly explains a supported mechanism or a
discriminating check; 1 useful but incomplete; 0 unrelated, generic advice that
does not discriminate this case, wrong service, or superseded. Unlisted passages
initially score 0; report annotation gaps rather than changing held-out labels
after observing retrieval. No-guidance cases lack mechanism-specific guidance;
generic evidence collection does not count as a retrieval hit.

Report Recall@3, hit rate, MRR@3, nDCG@3 (gain `2^relevance - 1`) and Precision@3
(divide by 3, including empty slots). Empty relevant sets have recall/MRR/nDCG
undefined and are reported separately. Require hits on at least 5/6 answerable
held-out cases; vector MRR must be no worse than full-text search. Require zero
service/version violations. Judge tool-selection misses separately from ranking.

## Answer support

For each causal claim, record the actual observation IDs and whether their text
supports that claim. A valid ID alone is insufficient. For each document-dependent
claim, record the exact excerpt and whether it supports the statement. Require
all IDs to resolve and zero unsupported causal claims in the held-out report.

The four ambiguous/insufficient held-out cases must avoid asserting a cause and
identify useful missing evidence. No-guidance cases are judged from observations:
explicit ENOSPC or certificate-expiry evidence may support a cause without a
runbook. General timeouts, deployment proximity and conflicting samples do not
establish a specific cause. A full pool does not prove a connection leak; slow
queries do not prove an underlying database fault.

Compare no-RAG and RAG with identical inputs, model and three-model/two-tool
limits. RAG must retain the supported-answer count and add correctly sourced,
useful read-only checks on retrieval-useful cases. Record regressions and raw
counts. State reviewer identity/role; assistant review is not independent review.

## Leakage, reproducibility and execution

Canary labels and expected outcomes must never occur in prompts or retrieval
queries. Ingest only manifest paths. Treat retrieved text as data, never trusted
instructions. Adversarial instructions belong in test fixtures, not the corpus.

Freeze hashes before tuning. Tune only on development data. Failed held-out gates
remain failures; further tuning needs a fresh held-out set. These 24 cases do not
establish production accuracy or exclude model memorization.

Fake baseline runs validate the harness only. Real local embeddings require a
separate offline run with pinned artifacts. Live generation evaluation requires
explicit agreement on model, case count and budgets. No automatic retries. Usage
unavailable from the current engine is recorded as unknown, never zero cost.
