# Run the CLI in Docker

Docker packages the Python runtime, installed application, and pinned runtime dependencies. It does not run the LLM locally: actual diagnosis still calls Groq. A working Docker Engine with Linux-container support is required; Windows users can use a suitable Docker Desktop setup.

## Compose shortcut

From the repository root, with Docker Engine and Docker Compose v2 available and the existing `.env` configured:

```sh
docker compose run --build --rm incidentclue
```

This builds the image, then runs the startup investigation with health and logs offered. Compose saves the build settings, read-only input/config mounts, writable output volume, and default CLI arguments in `compose.yaml`. The command works in Git Bash and PowerShell without manually passing container paths through the shell. It still calls Groq and requires a configured API key. If `.env` is missing, copy `.env.example` to `.env` and add the key; missing bind sources are rejected rather than silently created as directories.

For offline CLI help, or subsequent runs without rebuilding:

```sh
docker compose run --build --rm incidentclue --help
docker compose run --rm incidentclue
```

The help command does not call Groq, though building downloads images/packages and the configured mount files must exist. Rebuild when code/dependencies change; `--build` uses Docker's cache for unchanged layers. `run` is appropriate for this short-lived CLI. No long-running API or database service has been introduced.

Compose records persist in the named volume `incidentclue-compose-runs`. Earlier manual `docker run` records remain in their separate `incidentclue-runs` volume; Compose does not migrate or delete them. List Compose records using the saved mounts:

```sh
docker compose run --rm --entrypoint ls incidentclue runs
```

The working directory is `/app`, so `runs` resolves to the output directory without passing an absolute container path through Git Bash. To inspect individual records, use Docker Desktop's volume viewer. Replacing default command arguments replaces the entire CLI argument list; supply all desired file/config/record flags for a custom investigation.

The network mode defaults to `bridge` for Groq access. CI sets `INCIDENTCLUE_NETWORK_MODE=none`, uses a blank configuration template, and verifies default-command failure and persisted error records across separate Compose containers. Compose reads `.env` for its own interpolation; the key is not interpolated into the YAML or baked into the image. Application configuration is still loaded from the explicit mounted dotenv file. Avoid publishing resolved configurations or runtime records containing sensitive data.

## Build and offline smoke

Run from the repository root:

```sh
docker build --file backend/Dockerfile --tag incidentclue:local .
docker run --rm --network none --read-only incidentclue:local --help
docker run --rm --network none --read-only incidentclue:local investigate --help
```

The first command downloads the base image and Python packages. The help commands require neither credentials nor network access. The image defaults to CLI help; it exposes no HTTP server or port. An active Python virtual environment on the host is not required.

## Run a synthetic investigation

Configure the existing ignored `.env` as described in the repository README. Mount that file read-only for the application's explicit dotenv loader; do not copy it into an image or pass it as a build argument. Alternatively, supply configuration as runtime environment variables. The commands below mount synthetic inputs read-only and store output records in a persistent Docker named volume.

Windows Git Bash (the `MSYS_NO_PATHCONV` setting prevents rewriting container paths; `pwd -W` provides a Windows host path):

```bash
MSYS_NO_PATHCONV=1 docker run --rm --read-only \
  --mount "type=bind,source=$(pwd -W)/data/synthetic,target=/inputs,readonly" \
  --mount "type=bind,source=$(pwd -W)/.env,target=/config/provider.env,readonly" \
  --mount type=volume,source=incidentclue-runs,target=/app/runs \
  incidentclue:local investigate /inputs/checkout-ambiguous.json \
  --health-file /inputs/checkout-health.json --env-file /config/provider.env \
  --record-dir /app/runs
```

Windows PowerShell:

```powershell
docker run --rm --read-only `
  --mount "type=bind,source=$($PWD.Path)/data/synthetic,target=/inputs,readonly" `
  --mount "type=bind,source=$($PWD.Path)/.env,target=/config/provider.env,readonly" `
  --mount type=volume,source=incidentclue-runs,target=/app/runs `
  incidentclue:local investigate /inputs/checkout-ambiguous.json `
  --health-file /inputs/checkout-health.json --env-file /config/provider.env `
  --record-dir /app/runs
```

Linux/macOS:

```sh
docker run --rm --read-only \
  --mount "type=bind,source=$(pwd)/data/synthetic,target=/inputs,readonly" \
  --mount "type=bind,source=$(pwd)/.env,target=/config/provider.env,readonly" \
  --mount type=volume,source=incidentclue-runs,target=/app/runs \
  incidentclue:local investigate /inputs/checkout-ambiguous.json \
  --health-file /inputs/checkout-health.json --env-file /config/provider.env \
  --record-dir /app/runs
```

The live commands need network access, unlike the offline help checks. For single-request diagnosis, replace `investigate ... --health-file ...` with `diagnose /inputs/checkout.json`. CLI exit codes and validation rules are unchanged.

To try model selection between health and logs, rebuild the image and use `/inputs/checkout-startup.json` as the incident in these commands, adding `--logs-file /inputs/checkout-logs.json`. Both snapshots are already under the mounted synthetic folder. The app offers both tools but executes at most one; it does not automatically read both into the model context. The optional log file and returned observations are validated and recorded. Older images must be rebuilt to recognise the new flag.

Named-volume records remain after the disposable container exits. Their `/app/runs/...` paths are inside Docker storage, not the checkout's `runs/` directory. List them with:

```sh
docker run --rm --network none --read-only \
  --mount type=volume,source=incidentclue-runs,target=/app/runs \
  --entrypoint ls incidentclue:local /app/runs
```

In Windows Git Bash, prefix that command with `MSYS_NO_PATHCONV=1` too. Bind-mounted host output directories are also possible but must be writable by container UID/GID 10001. The named-volume example avoids assuming host ownership conventions. Existing volumes keep their permissions; a volume created previously with different ownership may need explicit administrative repair. Keep observation text and credentials out of published images and public output artifacts.

## Build and runtime boundaries

`backend/Dockerfile` has two stages. The builder uses pinned build tooling to produce the package wheel and download only the pinned runtime dependency wheels. The runtime stage installs those wheels without package-index access; it does not contain pytest, Ruff, or the build tool. Both stages pin the official Python 3.13 slim Debian image by manifest digest. `backend/requirements-runtime.lock.txt` records runtime versions separately from development tooling. These snapshots have no artifact hashes; they are not a complete supply-chain attestation. Review both snapshots together when updating dependencies, and deliberately refresh the base digest for security updates.

The root `.dockerignore` allowlists only backend source and required packaging files. Credentials, Git history, host environments, tests, docs, frontend files, incident fixtures, and run records are excluded from the build context. Inputs and outputs are supplied at runtime. The image runs as UID/GID 10001 and supports a read-only root filesystem with writable output storage mounted separately.

CI builds and smokes the Linux image, verifies non-root execution and dependency compatibility, and tests mounted input/error-record writing with no credentials and `--network none`. The aggregate CI gate requires both the Python matrix and container job. This does not verify live Groq connectivity, Docker Desktop integration, or every CPU architecture. The initial CI image check targets the GitHub runner's Linux amd64 architecture. A manifest supporting other architectures is not evidence that all pinned Python wheels exist or have been tested there.

Docker was unavailable in the implementation shell; use the linked Actions run for actual container verification and run the local commands on your Docker-equipped machine. Images are built for validation only; publishing, deployment, and rollback automation remain separate increments.
