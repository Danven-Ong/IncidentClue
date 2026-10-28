# IncidentClue

Investigate Software Outages with AI.

IncidentClue is designed to analyze logs, service health, and deployment events, then produce a structured diagnosis with supporting evidence and suggested diagnostic checks. The application is organized into a Python backend and a future web frontend.

## Status

Early development. The CLI can diagnose supplied evidence or optionally gather synthetic health or log observations through one model-requested tool call before diagnosis. Both paths validate business rules and citation membership and support local run records. A three-case synthetic pilot checked uncertainty; live health and log tool requests have succeeded. Human review and broader evaluation remain pending, along with the HTTP API and web frontend.

## Requirements

- Python 3.12 or newer
- pip

The initial environment was verified on Windows with Python 3.13.

## Installation

From the repository root, create an isolated environment:

```sh
python -m venv .venv
```

Install the package and development dependencies using the recorded version constraints.

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -c backend/requirements-dev.lock.txt -e "./backend[dev]"
```

macOS/Linux:

```sh
.venv/bin/python -m pip install -c backend/requirements-dev.lock.txt -e "./backend[dev]"
```

The dependency snapshot pins the current development package versions. It does not include artifact hashes or guarantee compatibility across all Python versions and platforms.

## Provider connectivity

Create local configuration from the template (PowerShell):

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Set GROQ_API_KEY in .env using a key from [Groq Console](https://console.groq.com/keys). The local .env file is ignored by Git; the template contains no credentials. GROQ_MODEL defaults to openai/gpt-oss-20b, subject to account access and current provider limits. Keep the account on the Free tier for the initial setup.

From the repository root, run:

```powershell
.\.venv\Scripts\python.exe -m incidentclue.smoke --env-file .env
```

On macOS/Linux, use .venv/bin/python in place of the Windows executable path. Existing environment variables take precedence over file values. Omit --env-file when configuration is already in the process environment.

The command sends a fixed connectivity prompt and checks for the strict JSON response {"status":"ok"}. It does not send incident data or perform a diagnosis. Success prints response/model identifiers, token usage when available, and observed request latency. Error output omits credentials and raw provider responses.

The client uses Groq's explicit API endpoint, a 20-second SDK timeout setting, zero automatic retries, and a 512-token completion budget. Exit codes: 0 for a validated response, 1 for provider/response failure, and 2 for configuration errors. This check establishes connectivity/output behavior only; it is not a quality benchmark.

The [recorded live smoke check](docs/validation/groq-smoke-2026-10-28.json) on 28 October 2026 returned the expected JSON with openai/gpt-oss-20b. Its request latency was 1,520.11 ms and reported usage was 246 tokens. This is one observation of connectivity, not a performance or diagnosis-accuracy benchmark.

## Diagnose an incident

From the repository root (PowerShell):

```powershell
.\.venv\Scripts\incidentclue.exe diagnose data/synthetic/checkout.json --env-file .env --record-dir runs
```

On macOS/Linux:

```sh
.venv/bin/incidentclue diagnose data/synthetic/checkout.json --env-file .env --record-dir runs
```

The equivalent module entry point is `python -m incidentclue.cli diagnose ...` using the installed environment's Python. Input paths are explicit. The command accepts UTF-8 JSON (including a UTF-8 BOM), limits the source file to 64,000 bytes, and applies the investigation's 12,000-byte serialized incident limit. Model selection follows `--model`, then `GROQ_MODEL`, then the default; existing process variables take precedence over values in the explicitly supplied environment file.

Standard output contains only the validated Diagnosis JSON. Errors and the optional record path go to standard error. Exit codes are 0 for an accepted diagnosis (including insufficient evidence), 1 for provider or rejected-output failures, 2 for input/configuration errors, and 3 when the requested run record cannot be written. A record-write failure can occur after a successful diagnosis has already been printed.

`--record-dir` is optional. Records use unique filenames and exclusive creation, preserving existing files. They include configuration, normalized input/instruction hashes, processing time, outcome, and the accepted diagnosis or a sanitized error. They omit credentials, raw incident contents, and raw provider errors. Incident identifiers and diagnosis text can still contain sensitive information: choose the directory accordingly. The default example directory `runs/` is ignored by Git. Records are local development artifacts, not crash-safe audit storage. Provider response IDs and usage are currently unavailable from the diagnosis function and are recorded as null. Processing time excludes writing the record and is not a hard request deadline.

See the [recorded CLI checkout run](docs/validation/cli-checkout-2026-10-28-1cef626a.json) for one actual result and its review limitations.

The [three-case pilot report](docs/validation/pilot-2026-10-28.md) preserves clear, ambiguous, and insufficient-evidence results, evaluator criteria, and limitations found in suggested checks. Expected outcomes live separately in `evals/pilot-cases.json` and are never included in incident inputs. Three synthetic examples do not establish general diagnosis accuracy.

## Investigate with a read-only tool

From the repository root in Git Bash (Windows):

```bash
./.venv/Scripts/incidentclue.exe investigate data/synthetic/checkout-ambiguous.json --health-file data/synthetic/checkout-health.json --env-file .env --record-dir runs
```

PowerShell uses `.\.venv\Scripts\incidentclue.exe`; macOS/Linux uses `.venv/bin/incidentclue`. The new command keeps the original incident separate from a recorded synthetic health snapshot. The first model request offers `get_service_health(service)` with automatic tool choice. If requested, Python validates the name and arguments, restricts the service to the incident service, and reads only the explicitly configured snapshot. No model-supplied path, URL, shell command, or arbitrary Python is executed. This is direct function calling, without MCP or an agent framework.

There are at most two model requests and one tool execution: selection, then a separate strict-JSON diagnosis. Health results are returned with the tool-call ID, and final citation validation includes successfully fetched observations. The model may skip the lookup; then only original evidence is available. Unavailable/unauthorized service lookups return structured errors with no observations. Invalid tool names/arguments, multiple calls, or evidence ID collisions fail explicitly, without repairs or retries. The CLI uses the same exit codes as `diagnose`. An oversized enriched incident is an input error even if the first request has already completed.

The health file is limited to 16,000 bytes, five health observations, and 2,000 characters of content per observation. The 12,000-byte incident budget also applies after enrichment; it does not measure the entire conversation or exact tokens. The two requests use separate 512-token selection and 1,024-token diagnosis budgets. Each has a 20-second SDK transport timeout; there is no hard overall deadline. The snapshot is timestamped test data, not current production monitoring.

Investigation records additionally contain tool events and returned health evidence, plus a normalized health-snapshot hash and selection/final instruction hashes. These records can contain sensitive observation text; keep them local unless deliberately reviewed for publication. The model cannot execute state-changing tools, but textual suggestions can still violate instructions or overstate evidence. See the [actual tool run and review](docs/validation/health-tool-2026-10-28.md).

To offer both health and logs, add `--logs-file` (Git Bash):

```bash
./.venv/Scripts/incidentclue.exe investigate data/synthetic/checkout-startup.json --health-file data/synthetic/checkout-health.json --logs-file data/synthetic/checkout-logs.json --env-file .env --record-dir runs
```

The model can choose either tool or skip lookup; only one tool may execute. `get_logs` requires service, `start_time`, and `end_time` with timezone offsets. Python rejects extra arguments, reversed/empty windows, and windows longer than one hour. Filtering is inclusive at both ends. The result contains the five most recent matching observations in timestamp/ID order, plus `truncated` when more matches exist. Empty results describe this snapshot only; they do not prove that no errors occurred. Only returned IDs become eligible citations, so out-of-window or truncated-away entries cannot be cited unless independently supplied in the original incident.

Log snapshots are limited to 64,000 file bytes, 20 unique log observations, timezone-aware timestamps, and 2,000 content characters per observation. The existing enriched-incident byte limit still applies. Omit `--logs-file` to keep the health-only behavior. Records include the normalized log snapshot hash when configured and actual returned log text when requested. See the [live two-tool selection review](docs/validation/log-tool-2026-10-28.md). Rebuild the Docker image after source changes; [container instructions](docs/containers.md) explain the extra option.

## Diagnose an incident from Python

From the repository root, after installing the backend and configuring .env, use the library function:

```python
import os
from pathlib import Path

from dotenv import load_dotenv

from incidentclue.diagnosis import diagnose_incident
from incidentclue.llm import DEFAULT_MODEL, create_groq_client
from incidentclue.schemas import Incident

load_dotenv(".env", override=False, encoding="utf-8-sig")
incident = Incident.model_validate_json(
    Path("data/synthetic/checkout.json").read_text(encoding="utf-8-sig")
)
with create_groq_client(os.environ["GROQ_API_KEY"]) as client:
    result = diagnose_incident(
        incident, client, os.environ.get("GROQ_MODEL", DEFAULT_MODEL)
    )
print(result.model_dump_json(indent=2))
```

The function sends one request with separate instructions and incident data, requests the Diagnosis JSON schema, then applies application consistency and citation checks. Serialized incident data is limited to 12,000 UTF-8 bytes, with a 1,024-token completion budget and automatic retries disabled. Oversized input, invalid output, and unknown references fail explicitly; provider errors remain SDK exceptions. Callers must handle these failures.

The [first live checkout result](docs/validation/checkout-diagnosis-2026-10-28-a4391347.json) identified the missing PAYMENT_API_URL configuration using evidence E2. It passed structural/reference validation and an assistant review of the supplied observations; independent human review is pending. One synthetic case does not establish accuracy across incidents.

## Docker

Build and run offline CLI help from the repository root:

```sh
docker build --file backend/Dockerfile --tag incidentclue:local .
docker run --rm --network none --read-only incidentclue:local --help
```

The image uses a digest-pinned Python base, pinned runtime dependencies, and a non-root user. It contains no provider credentials or incident fixtures. See [container instructions](docs/containers.md) for Git Bash, PowerShell, and Linux/macOS commands to mount inputs/configuration and retain run records. The runtime is packaged locally; model inference remains hosted by Groq.

## Tests

The [CI workflow](.github/workflows/ci.yml) runs on every push and pull request, including draft PRs. It checks Linux with Python 3.12/3.13 and Windows with Python 3.13. Each job installs pinned dependencies, lints the backend, builds the source distribution and wheel, installs that wheel, checks dependencies, and runs the offline test suite. An additional smoke check runs the installed CLI outside the checkout. A Linux container job builds the runtime image and checks non-root execution, dependencies, CLI help, mounted input, and error-record writing without credentials or container network access. The stable `CI gate` check succeeds only when the entire matrix and container job succeed. See [Actions runs](https://github.com/Danven-Ong/IncidentClue/actions) for actual results.

CI uses no provider secrets and makes no intentional live LLM requests; model/provider interactions in tests use fake transports. Package downloads still require network access. Live synthetic evaluations are separate, deliberate runs. Workflow actions are pinned to commit SHAs, repository permissions are read-only, and checkout credentials are not persisted.

To run lint and build locally with the installed development environment:

```sh
python -m ruff check --config backend/pyproject.toml backend
python -m build --no-isolation backend
```

Use your virtual environment's Python. Building requires the pinned development/build tools. CI tests the installed wheel rather than an editable source install; source-distribution tests and fixtures remain in the full repository checkout. Passing CI is a prerequisite for review, not an automatic deployment or merge. The repository's protected main branch requires the CI gate. Image publishing and deployment remain future steps.

From the repository root, run the backend tests with the development environment's Python.

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests
```

macOS/Linux:

```sh
.venv/bin/python -m pytest backend/tests
```

Tests cover accepted observation types, timestamps, required fields, nested validation, and unique evidence IDs within each incident. Output tests cover hypothesis/insufficient-evidence consistency, required nullable fields, malformed citation IDs, unexpected fields, and references absent from the supplied incident. Empty incident evidence lists are accepted so the investigation pipeline can handle insufficient-evidence cases. Citation membership checks do not establish whether an observation supports the claimed cause.

## Repository structure

```text
IncidentClue/
  backend/
    src/incidentclue/
      __init__.py
      schemas.py
      diagnosis.py
      investigation.py
      tools.py
      cli.py
      records.py
      llm.py
      smoke.py
    tests/
      conftest.py
      test_schemas.py
      test_incidents.py
      test_diagnoses.py
      test_diagnosis_validation.py
      test_smoke.py
      test_diagnosis_request.py
      test_cli.py
      test_investigation.py
      test_logs.py
    pyproject.toml
    requirements-dev.lock.txt
  frontend/
    README.md
  data/
    synthetic/
      checkout.json
      checkout-ambiguous.json
      checkout-insufficient.json
      checkout-health.json
      checkout-startup.json
      checkout-logs.json
  evals/
    pilot-cases.json
  docs/
    architecture.md
  .gitignore
  .env.example
  README.md
```

## Example incident

[data/synthetic/checkout.json](data/synthetic/checkout.json) contains a checkout-service incident with deployment, log, and health observations. Every observation has a stable evidence ID and timestamp.

The example is synthetic and can be supplied to the diagnostic command. An API endpoint is not available yet. Shared examples stay at the repository root, independent of either application.

## Application boundaries

The CLI and future FastAPI routes will call the same backend investigation logic. The frontend will communicate with the backend through its HTTP API; it will not import Python modules or call the model provider directly. Server-side credentials stay in backend configuration.

The root .venv is a local development environment. Python packaging, dependencies, and tests are owned by backend/. The frontend will own its own dependencies and build configuration when implemented. Each application can therefore be packaged independently.

## Architecture

See [docs/architecture.md](docs/architecture.md) for the proposed request flow, component responsibilities, and execution boundaries.
