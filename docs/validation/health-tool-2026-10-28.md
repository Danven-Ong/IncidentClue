# Synthetic health-tool investigation — 28 October 2026

One live `investigate` run used `openai/gpt-oss-20b` on Groq, with the [ambiguous incident](../../data/synthetic/checkout-ambiguous.json) and a separate [synthetic health snapshot](../../data/synthetic/checkout-health.json). The model requested the allowed function, Python returned health evidence, and a second model request produced a structurally accepted diagnosis. [Actual run record](health-tool-2026-10-28.json). Human review is pending.

The tool-enabled implementation was uncommitted over base `cef8619806defc461106ada397a8507144d25152` during execution. There was one live attempt with no repairs or automatic retries. The CLI used the explicitly configured local environment file. No credentials or raw provider errors are in the published record. Provider usage/response identifiers were unavailable and remain null.

## Observed flow

1. The model received original E1/E2/E3 and the health tool schema. The health snapshot was not included in that request.
2. It requested `get_service_health` with `{"service":"checkout"}`.
3. Python validated the tool and arguments, checked the incident service, and returned H1: a recorded readiness detail reports missing PAYMENT_API_URL. This read an in-memory synthetic snapshot; no live service was contacted or changed.
4. The second request included the assistant tool call and matching tool result. It offered no executable tools and requested strict Diagnosis JSON.
5. The accepted result proposed missing configuration and cited E1/E2/E3/H1. The CLI exited 0. Processing duration across the command was 2,789.73 ms, excluding record writing; this is one observation, not a performance benchmark.

## Assistant evidence review

H1 supports missing configuration as a plausible reason for checkout not being ready. E2 shows HTTP 503; E1 gives deployment context and E3 reports an unspecified timeout. Neither E1 nor E3 directly establishes the configuration cause. The response overstates the evidence by treating the configuration failure as the established cause of the request timeouts as well. The earlier ambiguous-only pilot abstained, but these are separate stochastic runs rather than a controlled causal experiment or accuracy measurement.

The model left missing_evidence empty, although the origin of the missing variable and the relationship to the timeout remain unknown. It also suggested ensuring the service is restarted with updated configuration, despite the final instructions requesting read-only checks. This is a prompt-adherence failure in textual advice. The tool dispatcher has no restart function and executed no remediation; valid JSON and existing citations did not detect this advice violation.

This run demonstrates actual model-requested tool execution and evidence propagation. It does not establish safe advice, diagnosis accuracy, arbitrary prompt-injection resistance, production authorization, or the reliability of future live integrations. Offline tests separately cover scope restrictions, malformed/unknown requests, budgets, unavailable data, provider failures, evidence collisions, and invalid citations. Before production use, constrain or review suggested actions and evaluate more cases; do not treat one successful tool call as proof of an autonomous investigator.
