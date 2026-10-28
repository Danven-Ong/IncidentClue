# IncidentClue

Investigate Software Outages with AI.

IncidentClue is designed to analyze logs, service health, and deployment events, then produce a structured diagnosis with supporting evidence and suggested diagnostic checks. The application is organized into a Python backend and a future web frontend.

## Status

Early development. The backend validates incident snapshots, nested evidence, structured diagnosis output, and citation membership against the supplied incident. Model integration, investigation logic, CLI/API entry points, and the web frontend are pending implementation.

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
    tests/
      conftest.py
      test_schemas.py
      test_incidents.py
      test_diagnoses.py
      test_diagnosis_validation.py
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
  README.md
```

## Example incident

[data/synthetic/checkout.json](data/synthetic/checkout.json) contains a checkout-service incident with deployment, log, and health observations. Every observation has a stable evidence ID and timestamp.

The example is synthetic. A diagnostic command or API endpoint is not available yet. Shared examples stay at the repository root, independent of either application.

## Application boundaries

The CLI and future FastAPI routes will call the same backend investigation logic. The frontend will communicate with the backend through its HTTP API; it will not import Python modules or call the model provider directly. Server-side credentials stay in backend configuration.

The root .venv is a local development environment. Python packaging, dependencies, and tests are owned by backend/. The frontend will own its own dependencies and build configuration when implemented. Each application can therefore be packaged independently.

## Architecture

See [docs/architecture.md](docs/architecture.md) for the proposed request flow, component responsibilities, and execution boundaries.
