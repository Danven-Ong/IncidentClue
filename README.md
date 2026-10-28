# IncidentClue

Investigate Software Outages with AI.

IncidentClue is designed to analyze logs, service health, and deployment events, then produce a structured diagnosis with supporting evidence and suggested diagnostic checks. The application is organized into a Python backend and a future web frontend.

## Status

Early development. The diagnostic CLI loads an incident file, requests a structured diagnosis through Groq, validates business rules and citation membership, and optionally saves a local run record. One synthetic checkout incident has been diagnosed successfully through the CLI. The HTTP API, web frontend, and broader diagnosis evaluation remain pending.

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

## Tests

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
    pyproject.toml
    requirements-dev.lock.txt
  frontend/
    README.md
  data/
    synthetic/
      checkout.json
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
