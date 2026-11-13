import { defineConfig } from '@playwright/test';

const upstream = process.env.FAKE_API_PORT ?? '8011';
const port = process.env.PROXY_PORT ?? '4175';

export default defineConfig({
  testDir: 'test',
  testMatch: 'proxy.security.ts',
  timeout: 60_000,
  expect: { timeout: 15_000 },
  use: { baseURL: `http://127.0.0.1:${port}` },
  webServer: [
    {
      command: '../../.venv/bin/python ../../scripts/fake_api.py',
      url: `http://127.0.0.1:${upstream}/healthz`,
      env: { FAKE_API_PORT: upstream, FAKE_API_DELAY: '0' },
      reuseExistingServer: false,
    },
    {
      command: 'node build/index.js',
      url: `http://127.0.0.1:${port}/`,
      env: {
        API_INTERNAL_URL: `http://127.0.0.1:${upstream}`,
        WEB_SERVICE_SECRET: 'web-secret',
        DEVELOPMENT_PROTECTED: 'false',
        PORT: port,
        HOST: '127.0.0.1',
        ORIGIN: `http://127.0.0.1:${port}`,
      },
      reuseExistingServer: false,
    },
  ],
});
