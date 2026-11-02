# CLI guide

Optional native RAG: after explicit corpus preparation, `start` accepts
`--runbooks CORPUS_SHA256`. Configure `INCIDENTCLUE_RAG_MODEL_DIR` for later local
lookup. Creation can remain paused with zero calls; inspection JSON includes the
saved retrieval pin, `state.retrieved_sources` and diagnosis `source_ids`.
The lookup competes for the existing two tool attempts. See the
[RAG setup and compatibility guide](rag.md) before opting in. Current Docker
application images retain their original non-RAG dependencies.

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

## One-shot example

```sh
docker compose run --rm incidentclue
```

This runs the bundled investigation example. JSON records persist in
`incidentclue-compose-runs`, separate from PostgreSQL checkpoints.

For command options, run `docker compose run --rm durable --help`.
