import { defineEnvVars } from '@sveltejs/kit/env';
export const variables = defineEnvVars({
  DEVELOPMENT_PROTECTED: { schema: (value) => value || 'false' },
  DEVELOPMENT_USER: { schema: (value) => value || '' },
  DEVELOPMENT_PASSWORD: { schema: (value) => value || '' },
  WEB_EDGE_AUTH_ENABLED: { schema: (value) => value || '' },
  WEB_SERVICE_SECRET: { schema: (value) => value || '' },
  API_INTERNAL_URL: { schema: (value) => value || 'http://localhost:8000' },
});
