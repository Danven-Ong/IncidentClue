import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  // Never load the backend .env, even when started from the repository root.
  envDir: false,
  server: {
    host: '127.0.0.1', port: 5173, strictPort: true,
    proxy: { '/api': {
      target: process.env.INCIDENTCLUE_API_TARGET || 'http://127.0.0.1:8000',
      rewrite: path => path.replace(/^\/api/, ''),
      timeout: 0, proxyTimeout: 0,
    } },
  },
  test: { environment: 'jsdom', include: ['src/**/*.test.{ts,tsx}'], restoreMocks: true },
});
