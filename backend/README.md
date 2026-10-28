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
