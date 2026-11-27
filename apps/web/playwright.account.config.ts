import { defineConfig } from '@playwright/test';

// Account mode is off in the default suite, so /sign-in is a 404 there. This
// config serves the public account pages only; the API is deliberately
// unreachable because these tests cover the form's client-side behaviour.
export default defineConfig({
  testDir: 'test',
  testMatch: 'account-form.spec.ts',
  fullyParallel: false,
  workers: 1,
  use: { baseURL: 'http://localhost:4179' },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 4179',
    url: 'http://127.0.0.1:4179/sign-in',
    timeout: 180_000,
    reuseExistingServer: false,
    env: {
      API_INTERNAL_URL: 'http://127.0.0.1:1',
      DEVELOPMENT_PROTECTED: 'true',
      ACCOUNT_ACCESS_ENABLED: 'true',
      ADDRESS_HEADER: 'x-forwarded-for',
      WEB_SERVICE_SECRET: 'test-only-proxy-secret',
    },
  },
});
