import { defineConfig } from '@playwright/test';
import { resolve } from 'node:path';

export default defineConfig({
  testDir: 'test',
  testMatch: 'auth-expired.spec.ts',
  fullyParallel: false,
  workers: 1,
  use: { baseURL: 'http://localhost:4178' },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 4178',
    url: 'http://127.0.0.1:4178',
    timeout: 180_000,
    reuseExistingServer: false,
    env: {
      API_INTERNAL_URL: 'http://127.0.0.1:1',
      DEVELOPMENT_PROTECTED: 'true',
      REVIEW_ACCESS_TOKEN: 'test-review-token-0123456789abcdef',
      REVIEW_ACCESS_MAX_USES: '2',
      // Absolute instant in the past: the token and every session it issues
      // are dead even when the cookie signature is valid.
      REVIEW_ACCESS_EXPIRES_AT: '2000-01-01T00:00:00Z',
      REVIEW_ACCESS_STATE_PATH: resolve('test-results/auth-expired-state.json'),
      SESSION_SECRET: 'test-session-secret-0123456789abcdef',
      WEB_SERVICE_SECRET: 'test-only-proxy-secret',
    },
  },
});
