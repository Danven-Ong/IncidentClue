# CLI guide

Use the CLI through Docker—no Python installation needed. Complete the
[main quick start](../README.md#quick-start), then run commands from the repository root.

## Saved investigations

**Create a paused investigation using the included example files:**

```sh
docker compose run --rm durable start /inputs/checkout-startup.json --health-file /inputs/checkout-health.json --logs-file /inputs/checkout-logs.json --paused --env-file /config/provider.env
```

Copy the returned investigation ID and replace `UUID` below:

| Action | Command |
| --- | --- |
| View progress | `docker compose run --rm durable inspect UUID` |
| Run or continue | `docker compose run --rm durable resume UUID --env-file /config/provider.env` |
| Request pause | `docker compose run --rm durable pause UUID` |

Docker mounts the example files at `/inputs/` and your root `.env` at
`/config/provider.env`. Running new model requests requires `GROQ_API_KEY`.

## Optional runbook retrieval

After the [RAG setup](rag.md#quick-start), add `--runbooks CORPUS_ID` to the
`durable start` command to select the ingested runbooks. Docker mounts the model
files read-only. The CLI requires this flag explicitly; the server's corpus
setting applies to browser/API creation.

## One-shot example

```sh
docker compose run --rm incidentclue
```

This runs the bundled investigation example. JSON records persist in
`incidentclue-compose-runs`, separate from PostgreSQL checkpoints.

For command options, run `docker compose run --rm durable --help`.
