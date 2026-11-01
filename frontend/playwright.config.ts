import { defineConfig } from '@playwright/test';

const external = process.env.INCIDENTCLUE_BROWSER_BASE_URL;
const database = process.env.INCIDENTCLUE_BROWSER_DATABASE_URL;
const python = process.env.INCIDENTCLUE_PYTHON || 'python';
export default defineConfig({
  testDir: './tests', timeout: 30_000, workers: 1, retries: 0,
  reporter: [['list']], use: { baseURL: external || 'http://127.0.0.1:5173',
    trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  webServer: [
    ...(!external ? [{ command: 'npm run dev', url: 'http://127.0.0.1:5173', reuseExistingServer: false,
      env: { INCIDENTCLUE_API_TARGET: 'http://127.0.0.1:8002' } }] : []),
    ...(database ? [{ command: `"${python}" tests/api_server.py`, url: 'http://127.0.0.1:8002/health',
      reuseExistingServer: false, env: { INCIDENTCLUE_TEST_DATABASE_URL: database, GROQ_API_KEY: '', OPENAI_API_KEY: '' } }] : []),
  ],
});
