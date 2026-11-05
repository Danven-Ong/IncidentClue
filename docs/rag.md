# Runbook retrieval (RAG)

Follow the [main Docker quick start](../README.md#quick-start) first.
RAG support is already included; the steps below enable retrieval.

## Quick start

From the repository root in PowerShell:

1. Set the directory containing the verified model files listed in
   [artifact.json](../backend/src/incidentclue/rag/artifact.json).

   ```powershell
   $env:INCIDENTCLUE_RAG_MODEL_DIR = 'C:\absolute\path\to\rag-model-probe'
   ```

2. Ingest the packaged runbooks. This stores their text and embeddings in the
   application's PostgreSQL database. Repeating it with unchanged inputs is a no-op.

   ```powershell
   docker compose run --rm rag-ingest
   ```

3. Set the corpus ID printed by ingestion and update the application.

   ```powershell
   $env:INCIDENTCLUE_RAG_CORPUS_ID = '<corpus_id from ingestion>'
   docker compose --profile frontend up -d --wait frontend
   ```

Open [IncidentClue](http://localhost:8080). New investigations can now retrieve
runbook guidance and save source citations.

`INCIDENTCLUE_RAG_CORPUS_ID` selects the stored set of runbooks to search.
It is empty by default, so retrieval is disabled. Setting it to a valid ingested
corpus ID enables retrieval for new investigations. Clear it and repeat the `up`
command to disable retrieval again. Existing investigations keep their saved settings.
Save the model directory and corpus ID in `.env` to keep them across terminal sessions.
