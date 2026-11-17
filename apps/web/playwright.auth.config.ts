import { defineConfig } from '@playwright/test';
import { resolve } from 'node:path';

const TOKEN = 'test-review-token-0123456789abcdef';
const SESSION_SECRET = 'test-session-secret-0123456789abcdef';

export default defineConfig({
  testDir: 'test',
  testMatch: 'auth.spec.ts',
  fullyParallel: false,
  workers: 1,
  use: { baseURL: 'http://localhost:4176' },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 4176',
    url: 'http://127.0.0.1:4176',
    timeout: 180_000,
    reuseExistingServer: false,
    env: {
      API_INTERNAL_URL: 'http://127.0.0.1:1',
      DEVELOPMENT_PROTECTED: 'true',
      REVIEW_ACCESS_TOKEN: TOKEN,
      REVIEW_ACCESS_MAX_USES: '2',
      REVIEW_ACCESS_EXPIRES_AT: '2099-01-01T00:00:00Z',
      REVIEW_ACCESS_STATE_PATH: resolve('test-results/auth-state.json'),
      SESSION_SECRET,
      WEB_SERVICE_SECRET: 'test-only-proxy-secret',
    },
  },
});
