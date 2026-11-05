# V1 RAG container verification — 5 November 2026

The optional v1 RAG delivery path was built and exercised locally on Docker
Desktop/Linux amd64, Python 3.13. No retrieval policy or corpus wording changed.
See [the operating instructions](../rag-container.md).

## Verified

- Both Docker targets built: default non-RAG and optional `rag`.
- Final RAG image ID:
  `sha256:2666c02a0a32827ee052a1e376db1a9137fb4fd6c65ac1180b4998a5405a5484`.
- The documented PowerShell helper built the final image from this checkout's
  allowlisted inputs. Direct context walking hit an inaccessible historical
  `.venv` pytest folder; the helper avoids it without changing ACLs or settings.
- Pinned RAG imports and `pip check` passed. Nonroot UID 10001 verified. Missing
  model failed closed with networking disabled. No model or v2 corpus is baked in.
- All 36 version 1 runbook passages embedded with existing verified model files mounted
  read-only, with Docker networking disabled. Maximum passage length: 47 tokens.
- Disposable PostgreSQL 17.8/vector 0.8.1: first ingestion published 36 passages;
  repeat ingestion returned `published: false`.
- The checked-in smoke override ran with an internal Docker network and a fresh
  test project. Real embeddings/SQL returned three sources, including the pool
  runbook, for the synthetic pool-wait incident.
- A non-RAG investigation was created with no retrieval pin. RAG creation charged
  zero attempts; retrieval paused after execution with one model/one tool charge.
- After database restart and a new backend container, saved retrieval was reused.
  Scripted fake-provider completion cited a saved source and retained incident
  evidence. Final accounting: three model attempts, one tool attempt.
- After a second database restart and new backend container, exact completed state
  was inspectable/resumable with retrieval and provider replay forbidden.
- The actual web service created a v1-pinned investigation over HTTP without
  generation, and exposed the completed smoke's sources and diagnosis citations.
- Frontend proxy health passed. Clearing the corpus setting and replacing web
  produced a new investigation with `retrieval: null` and zero charged attempts;
  frontend proxy health still passed after replacement.
- The exact new CI runtime check passed locally with networking disabled. The
  workflow parses and its gate includes the new RAG image job. Remote CI has not
  run for these uncommitted changes.
- Ruff passed. Focused corpus/retrieval/integration tests: 94 passed, 1 skipped.

## Boundaries and remaining limitations

No provider calls, model downloads, host package installs, commits, pushes or PR
changes. Pinned Python wheels were downloaded/installed only inside authorized
Docker build stages. No app database or existing persistent volumes were used.
Only the two named verification projects and their ownership-verified disposable
volumes were removed after retaining the synthetic smoke JSON outside Git.

The uncommitted v2 corpus and loader/test/doc changes were hash-verified into an
external backup and removed from the working tree before this implementation.
V1 documents, runtime Python source and frozen evaluator files match HEAD.

This proves packaging/execution on Linux amd64, not retrieval or diagnosis quality,
ARM compatibility or a hosted deployment. Cutoff 0.3 remains uncalibrated and RAG
is disabled by default. Existing v1 quality limitations remain unchanged. Models
must be supplied separately through the documented read-only artifact mount.
