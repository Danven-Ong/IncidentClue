# Live bounded LangGraph investigation — 29 October 2026

One actual run used Groq's openai/gpt-oss-20b with the existing startup incident and separate health/log snapshots. [Actual run record](langgraph-2026-10-29.json). The implementation was uncommitted over base `9a169df9493597f742f459929dfeb6d5cfca637c` during execution. There was one live attempt, without forced tool choice, repairs, or automatic retries. Human review remains pending.

## Observed execution

The graph followed `decide → execute → decide → diagnose → validate`. The model chose get_logs for checkout from 02:00 to 02:05 UTC, received L1/L2, then requested no additional lookup. The graph generated and validated a hypothesis citing L2/L1/E2. The command exited 0. Completed counts were one tool attempt and three model requests, with stop_reason model_finished. Command processing time was 5,737.02 ms excluding record writing; provider token usage and response IDs remain unavailable/null. This is not a latency benchmark.

The second decision received the first tool's result. Original/snapshot/instruction hashes, returned log filtering, and final citation membership were checked. The record includes LangGraph version 1.2.18 and actual node entries. Two-lookups-before-stop is covered by deterministic offline tests, not observed in this particular live run; the model was allowed to stop early.

## Assistant review

L2 directly supports missing PAYMENT_API_URL during startup. L1 records startup beginning, and E2 records the health symptom; they do not independently establish the cause. The model left missing_evidence empty despite the unknown upstream reason for absent configuration. Advice to check readiness after correcting configuration includes a condition involving change; no configuration correction, health probe, or remediation was executed. The dispatcher still exposes only read-only snapshot functions.

This demonstrates real stateful routing and evidence use for one synthetic path, not production accuracy, safe advice, prompt-injection resistance, or arbitrary multi-step reliability. No durable checkpoint, pause/resume, or cross-process budget restoration was involved. The raw observation was retained rather than tuned or rewritten to conceal limitations.
