import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: 'test',
  expect: { timeout: 15000 },
  use: { baseURL: 'http://127.0.0.1:4173' },
  webServer: [
    {
      command: '../../.venv/bin/uvicorn app.main:app --app-dir ../api --host 127.0.0.1 --port 8000',
      url: 'http://127.0.0.1:8000/healthz',
      env: {
        DATABASE_URL: 'sqlite+aiosqlite:///./browser-test.db',
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
      command: 'npm run dev -- --host 127.0.0.1 --port 4173',
      url: 'http://127.0.0.1:4173',
      env: { API_INTERNAL_URL: 'http://127.0.0.1:8000', DEVELOPMENT_PROTECTED: 'false' },
    },
  ],
});
