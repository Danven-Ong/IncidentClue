# Backend

Python + FastAPI service for incident investigation. Use the
[Docker quick start](../README.md#quick-start) to run the complete application.

## Native development

Requires Python 3.12+. From the repository root:

```sh
python -m venv .venv
```

Activate with `.\.venv\Scripts\Activate.ps1` in PowerShell or
`source .venv/bin/activate` on macOS/Linux, then install:

```sh
python -m pip install -c backend/requirements-dev.lock.txt -e "./backend[dev]"
```

Configure a root `.env` from [.env.example](../.env.example), preserving existing
values. Set `INCIDENTCLUE_DATABASE_URL` for PostgreSQL and `GROQ_API_KEY` for new
model requests. Start the native API:

```sh
docker compose --profile web up -d --wait postgres
python -m incidentclue.cli db-init --env-file .env
python -m uvicorn incidentclue.api.app:create_app --factory --host 127.0.0.1 --port 8000 --env-file .env --no-access-log
```

Process variables override the env file. Restart Uvicorn after configuration changes.

## Tests and package checks

With the development environment active, from the repository root:

```sh
python -m ruff check --config backend/pyproject.toml backend
python -m pytest backend/tests -q
python -m pip check
python -m build --no-isolation --outdir dist/backend backend
```

Tests use fake providers. Database cases require `INCIDENTCLUE_TEST_DATABASE_URL`
pointing to an isolated test database; otherwise they skip. They create tables
and records, so keep saved work separate.

RAG dependencies are pinned in `requirements-rag.lock.txt` and installed by the
standard backend Docker image. See the [RAG guide](../docs/rag.md) for setup and the
[evaluation summary](../evals/rag/README.md) for measured results and limitations.

See the [HTTP API](../docs/http-api.md), [workflow](../docs/workflow.md) and
[CLI guide](../docs/cli.md) for usage and behavior.
