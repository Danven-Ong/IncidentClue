# IncidentClue backend

Python package for structured software-incident diagnosis and bounded read-only investigation. Requires Python 3.12 or newer.

The installed command is `incidentclue`. Run `incidentclue --help` for available commands. Incident JSON and optional synthetic health snapshots are supplied through explicit paths; they are not bundled into the Python package. Provider credentials come from the process environment or an explicit `--env-file` and are never distributed with the package.

From the full repository root, install development dependencies with:

```sh
python -m pip install -c backend/requirements-dev.lock.txt -e "./backend[dev]"
python -m ruff check --config backend/pyproject.toml backend
python -m pytest backend/tests -q
python -m build --no-isolation backend
```

The source distribution and wheel contain the backend package; integration fixtures and the test suite belong to the full repository checkout. See the repository README for synthetic examples, configuration, known limitations, and CI details.

`start`, `inspect`, `pause`, and `resume` use PostgreSQL checkpoints through the
shared lifecycle service. Run `db-init` explicitly first. `diagnose` and
`investigate` remain database-independent. Configuration is read from process
environment or an explicit `--env-file`, never from a default directory search.
See [durable lifecycle usage](https://github.com/Danven-Ong/IncidentClue/blob/main/docs/checkpoint-resume.md).

PostgreSQL integration tests are skipped unless `INCIDENTCLUE_TEST_DATABASE_URL`
names an isolated test database. The tests create application/checkpointer tables
and test records there, including actual process-death and concurrency cases.
Routine tests use fake provider responses and require no live provider key.
