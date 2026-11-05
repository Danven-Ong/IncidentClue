# Containerized v1 RAG

The optional `rag` backend image includes application code, pinned CPU embedding
packages and version 1 runbooks. PostgreSQL 17/vector 0.8.1 runs separately. The
frontend/API use the existing durable lifecycle. Linux amd64/Python 3.13 is the
verified target. No host Python or local Python package directory is needed.

This packages the existing implementation, not a passing quality result. The
limitations in [rag.md](rag.md) remain. The explicit **0.3 cutoff is uncalibrated**.
V2 is deferred and is not included in the image or workflow.

## Build and prepare

Requires Docker Engine/Desktop with Compose v2, this checkout, and a directory
containing the existing model artifacts described by
`backend/src/incidentclue/rag/artifact.json`. The loader verifies file size,
SHA256 and package versions before use. Missing/corrupt artifacts fail without a
download fallback. Model acquisition is a separate explicit preparation step.

Set the absolute model directory in the current terminal. For PowerShell:

```powershell
$env:INCIDENTCLUE_RAG_MODEL_DIR = 'C:\absolute\path\to\rag-model-probe'
```

For POSIX shells: `export INCIDENTCLUE_RAG_MODEL_DIR=/absolute/path/to/model`.
The directory must be readable by container UID 10001. It mounts read-only at
`/models/embedding`. Do not overwrite an existing `.env` to set this variable.

Build on Windows with the allowlisted context helper. It avoids unrelated old
test directories with inaccessible ACLs; it changes no permissions and needs no
host Python:

```powershell
./scripts/Build-RagImage.ps1
docker compose -f compose.rag.yaml build frontend
```

On a clean Linux checkout, `docker compose -f compose.rag.yaml build web frontend`
builds the same image inputs directly. Then prepare the database:

```sh
docker compose -f compose.rag.yaml run --rm --no-deps admin validate --root /opt/incidentclue --max-distance 0.3
docker compose -f compose.rag.yaml up -d --wait postgres
docker compose -f compose.rag.yaml run --rm rag-ingest
```

Builds acquire pinned wheels from `backend/requirements-rag.lock.txt`, install
them offline into the image, and run `pip check`. Models and secrets are excluded
from the build context. The v1 manifest/documents are baked into
`/opt/incidentclue/data/runbooks`. Runtime is nonroot with a read-only filesystem
and writable temporary storage. Startup and requests install/download nothing.

Ingestion explicitly runs retrieval schema setup first. Expect 36 passages and
corpus ID `3d0820c28e00e9ec5b4e400e7d38e2898cc9ab02c04958e2532d3443f6c7a3c0`.
Repeating ingestion returns `published: false`. The v1-only CLI selects the version 1
runbook manifest through the packaged root; no v2 selector is needed or shipped.

## Run and opt into retrieval

```sh
docker compose -f compose.rag.yaml up -d --wait frontend
```

Open the UI at `http://localhost:8081` or API docs at
`http://localhost:8001/docs`. This standalone `incidentclue-rag` project has a
separate network and `rag-postgres-data` volume. PostgreSQL is not published to
the host. **Do not merge this file into `compose.yaml`**. Existing app storage is
not migrated or adopted. These are local deployment credentials and ports;
configure secrets and access appropriately for another deployment environment.

RAG is disabled for new investigations by default. Enable it after ingestion:

```powershell
$env:INCIDENTCLUE_RAG_CORPUS_ID = '3d0820c28e00e9ec5b4e400e7d38e2898cc9ab02c04958e2532d3443f6c7a3c0'
docker compose -f compose.rag.yaml up -d --wait frontend
```

For POSIX use `export INCIDENTCLUE_RAG_CORPUS_ID=...`. Clear the variable and run
the same `up` command to disable RAG for new investigations. Existing saved pins
remain unchanged. Live generation separately needs a configured `GROQ_API_KEY`;
embeddings and ingestion need no provider. The LLM stays external; embeddings run
inside the backend container.

```sh
docker compose -f compose.rag.yaml run --rm --no-deps admin inspect --corpus 3d0820c28e00e9ec5b4e400e7d38e2898cc9ab02c04958e2532d3443f6c7a3c0
docker compose -f compose.rag.yaml run --rm --no-deps admin lookup --corpus 3d0820c28e00e9ec5b4e400e7d38e2898cc9ab02c04958e2532d3443f6c7a3c0 --service search --query "Pool acquisition waits while connections are at the limit" --model-dir /models/embedding
docker compose -f compose.rag.yaml stop frontend web postgres
```

Stop or `down` preserves the database volume. Do not use `down -v` on retained
data. The standard non-RAG `compose.yaml` workflow remains available.

## Offline end-to-end smoke

Supply the model path and build the RAG image above. Use a **fresh** explicit test
project name for each run; records refuse overwrites. This override creates an
internal Docker network without external egress. No provider credentials needed.

```sh
docker compose -f compose.rag.yaml -f compose.rag.smoke.yaml -p incidentclue-rag-smoke up --no-build --pull never -d --wait storage-setup
docker compose -f compose.rag.yaml -f compose.rag.smoke.yaml -p incidentclue-rag-smoke run --rm --pull never rag-ingest
docker compose -f compose.rag.yaml -f compose.rag.smoke.yaml -p incidentclue-rag-smoke run --rm --pull never rag-ingest
docker compose -f compose.rag.yaml -f compose.rag.smoke.yaml -p incidentclue-rag-smoke run --rm --pull never --entrypoint python admin /smoke.py begin --record /results/smoke.json
docker compose -f compose.rag.yaml -f compose.rag.smoke.yaml -p incidentclue-rag-smoke restart postgres
docker compose -f compose.rag.yaml -f compose.rag.smoke.yaml -p incidentclue-rag-smoke run --rm --pull never --entrypoint python admin /smoke.py complete --record /results/smoke.json
docker compose -f compose.rag.yaml -f compose.rag.smoke.yaml -p incidentclue-rag-smoke restart postgres
docker compose -f compose.rag.yaml -f compose.rag.smoke.yaml -p incidentclue-rag-smoke run --rm --pull never --entrypoint python admin /smoke.py inspect --record /results/smoke.json
docker compose -f compose.rag.yaml -f compose.rag.smoke.yaml -p incidentclue-rag-smoke down
```

Checks: real embeddings/SQL, repeat ingestion, non-RAG creation, saved RAG sources
after restart, scripted diagnosis citations, three model/one tool charged attempts,
and completed inspection/resume after another restart with no retrieval/provider
replay. The fake provider is test-only, not a production endpoint or mode.
Read `/results/smoke.json` with `--entrypoint cat admin /results/smoke.json` before
stopping if you want the full record. Test output lives in its own named volume.

CI builds the RAG target and checks dependency consistency, pinned imports,
nonroot identity, packaged v1 validation and fail-closed missing-model behavior
with networking disabled at runtime. Real-model smoke requires separately
supplied artifacts; CI does not download models or inherit a local model cache.
