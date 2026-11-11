import { defineConfig } from '@playwright/test';
import base from './playwright.config';

export default defineConfig({
 ...base,
 outputDir:'../../artifacts/test-videos/ui-raw',
 projects:[{name:'recorded-ui'}],
 use:{...base.use,baseURL:'http://127.0.0.1:4180',viewport:{width:1280,height:800},video:{mode:'on',size:{width:1280,height:800}},launchOptions:{slowMo:180}},
 webServer:[
  {command:'../../.venv/bin/uvicorn app.main:app --app-dir ../api --host 127.0.0.1 --port 8001',url:'http://127.0.0.1:8001/healthz',env:{DATABASE_URL:'sqlite+aiosqlite:///./browser-video.db',COOKIE_SECURE:'false',OPENAI_API_KEY:'',ELEVEN_API_KEY:'',DEEPGRAM_API_KEY:'',DEEPSEEK_API_KEY:'',LIVEKIT_API_KEY:'',LIVEKIT_API_SECRET:'',AGENT_SERVICE_SECRET:''}},
  {command:'npm run dev -- --host 127.0.0.1 --port 4180 --strictPort',url:'http://127.0.0.1:4180',env:{API_INTERNAL_URL:'http://127.0.0.1:8001'}}
 ]
});
