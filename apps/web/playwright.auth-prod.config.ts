import { defineConfig } from '@playwright/test';
import { resolve } from 'node:path';

const TOKEN = 'test-review-token-0123456789abcdef';

export default defineConfig({
  testDir: 'test',
  testMatch: 'auth-prod.spec.ts',
  fullyParallel: false,
  workers: 1,
  use: { baseURL: 'http://localhost:4181' },
  webServer: {
    // Built adapter-node output, not `vite dev`: production CSRF is what the
    // gate must survive.
    command: 'npm run build && node server.js',
    url: 'http://127.0.0.1:4181/healthz',
    timeout: 300_000,
    reuseExistingServer: false,
    env: {
      HOST: '127.0.0.1',
      PORT: '4181',
      // adapter-node defaults the request origin to https; this local HTTP
      // build declares its real origin so form Posts match CSRF self-origin.
      ORIGIN: 'http://localhost:4181',
      API_INTERNAL_URL: 'http://127.0.0.1:1',
      DEVELOPMENT_PROTECTED: 'true',
      REVIEW_ACCESS_TOKEN: TOKEN,
      REVIEW_ACCESS_MAX_USES: '10',
      REVIEW_ACCESS_EXPIRES_AT: '2099-01-01T00:00:00Z',
      REVIEW_ACCESS_STATE_PATH: resolve('test-results/auth-prod-state.json'),
      SESSION_SECRET: 'test-session-secret-0123456789abcdef',
      WEB_SERVICE_SECRET: 'test-only-proxy-secret',
    },
  },
});
