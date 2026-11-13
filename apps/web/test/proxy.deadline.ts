import { test, expect } from '@playwright/test';
import { PROXY_TIMEOUT_MS, SESSION_START_TIMEOUT_MS, proxyTimeoutMs } from '../src/lib/proxy-deadline';

test('deadline policy only extends session creation', () => {
  expect(proxyTimeoutMs('v1/voice/sessions', 'POST')).toBe(SESSION_START_TIMEOUT_MS);
  expect(proxyTimeoutMs('v1/voice/sessions', 'GET')).toBe(PROXY_TIMEOUT_MS);
  expect(proxyTimeoutMs('v1/voice/sessions/fake/end', 'POST')).toBe(PROXY_TIMEOUT_MS);
  expect(proxyTimeoutMs('v1/conversations', 'POST')).toBe(PROXY_TIMEOUT_MS);
});

// The fake upstream holds every API response for 20s. A slow dispatch must reach
// the client instead of being cut off at the normal 15s deadline.
test('slow session creation survives the startup deadline', async ({ request }) => {
  test.setTimeout(90_000);
  const started = Date.now();
  const response = await request.post('/api/v1/voice/sessions', {
    headers: { origin: 'http://127.0.0.1:4174', 'content-type': 'application/json' },
    data: { conversation_id: 'fake' },
  });
  const elapsed = Date.now() - started;
  expect(response.status()).toBe(201);
  expect(elapsed).toBeGreaterThan(18_000);
  expect(elapsed).toBeLessThan(45_000);
});

test('normal requests keep the shorter deadline', async ({ request }) => {
  test.setTimeout(90_000);
  const started = Date.now();
  const response = await request.get('/api/v1/conversations');
  const elapsed = Date.now() - started;
  expect(response.status()).toBe(503);
  expect((await response.json()).detail.code).toBe('API_UNAVAILABLE');
  expect(elapsed).toBeGreaterThan(14_000);
  expect(elapsed).toBeLessThan(30_000);
});
