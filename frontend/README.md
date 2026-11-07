# Frontend

React + TypeScript + Vite interface. Use the
[Docker quick start](../README.md#quick-start) to run the complete application.

## Development

Requires Node 24.15+ within the Node 24 line and a running API.
Use the main Docker setup or [native backend setup](../backend/README.md#native-development).
From `frontend/`:

```sh
npm ci --ignore-scripts
npm run dev
```

Open the [Vite client](http://127.0.0.1:5173). In PowerShell, use `npm.cmd` and
`npx.cmd` if script policy blocks the standard launchers.

Vite strips `/api` and forwards requests to `http://127.0.0.1:8000`.
Set `INCIDENTCLUE_API_TARGET` to change the target. Credentials stay on the backend. See the
[HTTP API guide](../docs/http-api.md) for endpoints.

## Tests

From `frontend/`:

```sh
npm run lint
npm test
npm run build
npx playwright install chromium
npm run test:browser
```

The build writes to `dist/`. Tests use fake providers. The real API browser case
requires `INCIDENTCLUE_BROWSER_DATABASE_URL` pointing to an isolated test database
and the backend development environment; otherwise it skips. Set
`INCIDENTCLUE_PYTHON` to select its Python executable if needed.
See [Playwright configuration](playwright.config.ts) for fixture ports and proxy setup.
