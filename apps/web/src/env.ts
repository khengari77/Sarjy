import { defineEnvVars } from '@sveltejs/kit/env';
export const variables = defineEnvVars({
  // Unset means unprotected; an explicitly empty or misspelled value fails boot.
  DEVELOPMENT_PROTECTED: { schema: (value) => value },
  // Legacy Basic-auth credentials. No longer read by the gate; kept declared so
  // older deployments can roll forward without touching their env files.
  DEVELOPMENT_USER: { schema: (value) => value || '' },
  DEVELOPMENT_PASSWORD: { schema: (value) => value || '' },
  // Reviewer invitations are a second door beside the shared access token.
  // Keep this in step with the API's REVIEWER_ACCESS_ENABLED setting.
  REVIEWER_ACCESS_ENABLED: { schema: (value) => value || 'false' },
  // Accounts replace both doors above. Keep in step with the API's setting.
  ACCOUNT_ACCESS_ENABLED: { schema: (value) => value },
  REVIEW_ACCESS_TOKEN: { schema: (value) => value || '' },
  REVIEW_ACCESS_MAX_USES: { schema: (value) => value || '20' },
  REVIEW_ACCESS_EXPIRES_AT: { schema: (value) => value || '' },
  REVIEW_ACCESS_STATE_PATH: { schema: (value) => value || '' },
  SESSION_SECRET: { schema: (value) => value || '' },
  WEB_SERVICE_SECRET: { schema: (value) => value || '' },
  API_INTERNAL_URL: { schema: (value) => value || 'http://localhost:8000' },
});
