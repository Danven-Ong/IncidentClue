# Frontend

Local React/TypeScript client over the existing FastAPI lifecycle. Create an
incident, reopen its UUID, inspect evidence/diagnosis, and explicitly resume or
request cooperative pause. The CLI remains supported. See the
[client contract](../docs/frontend.md) and [HTTP contract](../docs/http-api.md).

## Development

Use Node 24.15 or later in the Node 24 line (CI/image use 24.16.0). From this
directory, run:

```sh
npm ci --ignore-scripts
npm run dev
```

On Windows PowerShell, use `npm.cmd` and `npx.cmd` if script execution policy
blocks their `.ps1` launchers. Open `http://127.0.0.1:5173`. Start PostgreSQL,
explicitly initialize storage and start Uvicorn using the backend instructions.
The default proxy target is `http://127.0.0.1:8000`; override the server-only
`INCIDENTCLUE_API_TARGET` environment variable for another local API port. No
frontend env file is loaded. Never pass credentials as Vite variables.

## Built local client

With Docker Desktop running, use this one command from the repository root:

```sh
docker compose --profile frontend up --build -d --wait frontend
```

Open `http://127.0.0.1:8080`; `INCIDENTCLUE_FRONTEND_PORT` overrides that port.
Compose starts PostgreSQL, runs the dedicated `storage-setup` container, then
starts the API and frontend after their prerequisites succeed. The setup process
creates fresh tables and exits; it does not invoke the investigation CLI. Repeat
startup preserves saved investigations. Incompatible schemas stop startup and
require administrative review; no automatic schema upgrade or reset is performed.

No `.env` file or provider key is required to open the site, create a paused
investigation or inspect saved work. To execute a new investigation, configure
`GROQ_API_KEY` on the backend using the shell or an optional root `.env` file;
Compose reads it without mounting that file into the web container. Optional
`GROQ_MODEL` is also server-only. Never put provider credentials in frontend
variables. The CLI remains an optional client, not a website prerequisite.
After changing provider settings, rerun the startup command so Compose applies
the updated server environment.
The frontend container serves the build and proxies `/api` to the existing web
service. No Node runtime or provider secrets are shipped in that container.
CORS remains disabled; host ports bind to loopback. This has no public-access
controls and is not a deployment. `npm run dev` is the development server, not a
production static server.

For later startups without code changes, `docker compose --profile frontend up
-d --wait frontend` reuses the images. Run the command with `--build` after code
changes.

Stop using `docker compose --profile frontend stop frontend web postgres`.
Volumes are retained. Never use `down -v` to pause work.

## Checks

```sh
npm run build
npm run lint
npm test
npx playwright install chromium
npm run test:browser
```

Browser protocol/error tests run without backend services. To include the real
API test, set `INCIDENTCLUE_BROWSER_DATABASE_URL` to an **isolated test database**.
The fixture initializes it, creates investigations and uses fake SDK responses;
it never reads the application `.env` or calls a live provider. Python must have
the backend wheel and development dependencies installed; override
`INCIDENTCLUE_PYTHON` with an absolute Python executable if needed. The fixture
uses port 8002 and development client port 5173. Set
`INCIDENTCLUE_BROWSER_BASE_URL` to test the same flows through a built local proxy
instead of starting Vite; its API target must point to the test fixture.

Pinned development tooling overrides minimatch to 11.0.1 to address
GHSA-g3hv-75wg-m249; lint/build/tests exercise that resolution. No browser runtime
dependency is changed by this override. No automatic package-manager upgrades.
