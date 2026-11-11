import { defineConfig } from '@playwright/test';

const upstream = process.env.FAKE_API_PORT ?? '8010';

export default defineConfig({
 testDir: 'test',
 testMatch: 'proxy.deadline.ts',
 timeout: 90_000,
 expect: { timeout: 20_000 },
 use: { baseURL: 'http://127.0.0.1:4174' },
 webServer: [
  {
   command: '../../.venv/bin/python ../../scripts/fake_api.py',
   url: `http://127.0.0.1:${upstream}/healthz`,
   env: { FAKE_API_PORT: upstream, FAKE_API_DELAY: '20' }
  },
  {
   command: 'npm run dev -- --host 127.0.0.1 --port 4174',
   url: 'http://127.0.0.1:4174',
   env: { API_INTERNAL_URL: `http://127.0.0.1:${upstream}`, DEVELOPMENT_PROTECTED: 'false' }
  }
 ]
});
