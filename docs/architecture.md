# Architecture

IncidentClue is designed as a web application with a modular Python backend. It uses operational evidence to produce a structured hypothesis, supporting references, and suggested diagnostic checks. The repository groups independently packaged applications under backend/ and frontend/.

## Current implementation

The backend implements input/output models, a citation-membership guard, and a Groq connectivity smoke command. Offline tests exercise the installed SDK through a fake HTTP transport, and one live strict-JSON smoke request succeeded. The investigation pipeline and diagnostic request are pending; there is no incident CLI, HTTP API, frontend application, or database connection yet.

## Incident input contract

| Field | Rule |
| --- | --- |
| incident_id | Required, nonempty string identifying the incident |
| service | Required, nonempty string identifying the affected service |
| summary | Required, nonempty description of the reported symptoms |
| evidence | Required list of validated Evidence records; may be empty |

Each Evidence record requires a nonempty id and content, a parseable timestamp, and a kind of deployment, log, or health. Evidence IDs must be unique within an incident so a citation identifies exactly one observation. Separate incidents may reuse the same evidence IDs; references therefore need the incident context.

An explicitly empty list represents a snapshot with no observations. A missing or null evidence field is rejected as malformed input. Input validity does not establish the truth of observations or the cause of an incident.

## Diagnosis output contract

All five fields must be supplied, including probable_cause when its value is null. Unexpected fields are rejected.

| Field | Rule |
| --- | --- |
| status | hypothesis or insufficient_evidence |
| probable_cause | Required string or null; a hypothesis requires nonblank text, while insufficient_evidence requires null |
| evidence_ids | Required list of nonblank strings; a hypothesis requires at least one ID |
| missing_evidence | Required list of strings identifying information still needed |
| next_checks | Required list of strings describing suggested diagnostic checks |

An insufficient-evidence result may cite inconclusive observations without asserting a cause. Suggested checks are text only and do not authorize execution.

The schema checks structure and cross-field consistency. The separate validate_evidence_references(incident, diagnosis) function in backend/src/incidentclue/diagnosis.py checks whether every cited ID exists in the explicitly supplied incident. Callers must run both schema validation and this guard before accepting a result; the guard is not invoked automatically by the schema.

The guard uses exact, case-sensitive ID matching and raises ValueError listing unknown IDs in deterministic order. It checks citations even when the result is insufficient_evidence, accepts an empty citation list when that status permits it, and does not change either object. Its time complexity is linear in the number of input/cited IDs, plus sorting unknown IDs for the error message.

An ID present only in a different incident fails this check. Because IDs may be reused across incidents, the caller must associate the result with the correct input snapshot. Membership alone does not prove origin, truth, or semantic support; diagnosis quality requires separate evaluation.

## Proposed request flow

1. Validate an incident snapshot containing logs, health observations, and deployment events.
2. Send the bounded evidence to an LLM through a provider API.
3. Validate the response structure and verify that cited evidence IDs exist in the input.
4. Return a hypothesis or an insufficient-evidence outcome, together with supporting references and suggested checks.

The initial inference provider is Groq, accessed through the OpenAI Python SDK. llm.py creates a client with the fixed Groq endpoint, an explicit SDK timeout, and no automatic retries. smoke.py requests one tiny strict JSON object, checks completion/refusal/content, and emits credential-free metadata. Runtime configuration comes from process environment variables or an explicitly supplied local file; application code does not assume the repository working directory to find secrets.

The default model is openai/gpt-oss-20b, listed by Groq for strict structured output. One live request verified access and the connectivity response schema; future environments must run their own check. Provider timeouts/rate limits/authentication errors are separate from output-validation failures. The timeout is an SDK transport setting, not a hard wall-clock deadline. Documentation: [Groq compatibility](https://console.groq.com/docs/openai), [Structured Outputs](https://console.groq.com/docs/structured-outputs).

## Component boundaries

| Location | Responsibility |
| --- | --- |
| backend/src/incidentclue/ | Python package; data models and investigation logic |
| backend/tests/ | Backend tests and shared test fixtures |
| backend/pyproject.toml | Python packaging, dependencies, and test configuration |
| frontend/ | Web client with its own future dependencies and build configuration |
| data/synthetic/ | Synthetic incident snapshots for local development and demonstrations |
| docs/ | Application architecture and technical documentation |

Keep the core investigation logic independent of CLI and HTTP entry points. Add retrieval, tool execution, orchestration, and persistence when implemented rather than introducing empty modules in advance.

## Extension strategy

The following boundaries describe planned implementation, not existing features:

```mermaid
flowchart LR
    Web[Web frontend] --> API[FastAPI routes]
    API --> Core[Investigation logic]
    CLI[CLI entry point] --> Core
    Core --> Models[Model integration]
    Core --> Tools[Diagnostic tools]
    Core --> Retrieval[Evidence retrieval]
    Retrieval --> Store[(PostgreSQL and pgvector)]
```

- Entry points translate input/output and handle interface-specific errors. They do not own investigation decisions or duplicate business logic.
- The investigation code coordinates evidence collection and diagnosis. It should not depend on frontend types, HTTP request objects, or CLI arguments.
- Define explicit input/output schemas at the backend boundary. When HTTP endpoints exist, use their API schema as the contract for the frontend, with contract validation before incompatible changes.
- Keep SDK calls and database queries in integration modules as those features arrive. Introduce interfaces when multiple implementations or concrete testing needs justify them.
- Tests live alongside their owning application. Keep backend unit checks independent of live model services; add integration checks for real provider/database behavior separately.
- Use configurable paths for shared incident data. Runtime code must not assume that its working directory is the repository root; installed applications need explicit data paths or packaged resources.
- Add frontend build files, backend container files, root orchestration, and CI jobs when their corresponding runnable components exist. Preserve independent builds instead of coupling Python and frontend package managers.

The intended development path is a CLI over the investigation engine, then a FastAPI interface over the same engine, then a web client. A queue or persistent job API can be introduced if measured investigation duration requires asynchronous execution; it is not a prerequisite for the first request.

## Evidence and execution boundaries

Logs and retrieved content are untrusted input. Valid response structure and resolvable evidence IDs do not establish that a diagnosis is correct; evidence support requires separate evaluation. Expected root-cause labels must remain separate from model-visible incident snapshots.

Initial diagnostic tools will be read-only. Any future state-changing action must require application-enforced authorization and explicit human approval.
