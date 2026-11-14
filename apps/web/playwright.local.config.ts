import { defineConfig } from '@playwright/test';

// Temporary local config: the shared 8000/4173 pair is held by another
// worktree's browser run. This uses a private pair and is deleted after the
// local evidence run; it is never committed.
export default defineConfig({
  testDir: 'test',
  expect: { timeout: 15000 },
  use: { baseURL: 'http://127.0.0.1:4273' },
  webServer: [
    {
      command:
        '../../.venv/bin/uvicorn app.main:create_app --factory --app-dir ../api --host 127.0.0.1 --port 8100',
      url: 'http://127.0.0.1:8100/healthz',
      env: {
        DATABASE_URL: 'sqlite+aiosqlite:///./browser-test-local.db',
        COOKIE_SECURE: 'false',
        DEVELOPMENT_PROTECTED: 'false',
        OPENAI_API_KEY: '',
        ELEVEN_API_KEY: '',
        DEEPGRAM_API_KEY: '',
        DEEPSEEK_API_KEY: '',
        LIVEKIT_API_KEY: '',
        LIVEKIT_API_SECRET: '',
        AGENT_SERVICE_SECRET: '',
      },
    },
    {
      command: 'npm run dev -- --host 127.0.0.1 --port 4273',
      url: 'http://127.0.0.1:4273',
      env: { API_INTERNAL_URL: 'http://127.0.0.1:8100', DEVELOPMENT_PROTECTED: 'false' },
    },
  ],
});
