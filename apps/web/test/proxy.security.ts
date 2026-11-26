import { test, expect } from '@playwright/test';

const ORIGIN = `http://127.0.0.1:${process.env.PROXY_PORT ?? '4175'}`;

test('built proxy forwards a legitimate path and upstream query', async ({ request }) => {
  const response = await request.get('/api/v1/echo?q=hello');
  expect(response.status()).toBe(200);
  expect(await response.json()).toMatchObject({ path: '/v1/echo?q=hello' });
});

test('built proxy never forwards worker or caller credentials', async ({ request }) => {
  const response = await request.get('/api/v1/echo', {
    headers: {
      'x-agent-secret': 'worker-secret',
      authorization: 'Bearer caller-secret',
      cookie: 'identity=value',
    },
  });
  expect(response.status()).toBe(200);
  const body = await response.json();
  expect(body.forwarded).toContain('x-web-service-secret');
  expect(body.forwarded).toContain('cookie');
  expect(body.forwarded).not.toContain('x-agent-secret');
  expect(body.forwarded).not.toContain('authorization');
});

test.describe('built proxy traversal refusal', () => {
  for (const path of [
    '/api/v1/%252e%252e/internal/example',
    '/api/v1/%252E%252E/internal/example',
    '/api/v1/..%2finternal',
    '/api/v1//internal',
    '/api/v1/%252e%252e%252finternal',
  ]) {
    test(`rejects ${path}`, async ({ request }) => {
      const response = await request.get(path);
      expect(response.status()).toBe(404);
    });
  }
});

test('built proxy still enforces same-origin POSTs', async ({ request }) => {
  const rejected = await request.post('/api/v1/echo', {
    headers: { origin: 'https://untrusted.example' },
    data: {},
  });
  expect(rejected.status()).toBe(403);
  expect((await rejected.json()).detail.code).toBe('INVALID_ORIGIN');

  const accepted = await request.post('/api/v1/echo', {
    headers: { origin: ORIGIN },
    data: {},
  });
  expect(accepted.status()).toBe(200);
  expect((await accepted.json()).path).toBe('/v1/echo');
});

test.describe('built proxy DELETE', () => {
  test('forwards a same-origin DELETE with the service secret, the cookie and no body', async ({ request }) => {
    const response = await request.delete('/api/v1/memories/m-1', {
      headers: { origin: ORIGIN, cookie: 'identity=value' },
    });
    expect(response.status()).toBe(200);
    const body = await response.json();
    expect(body).toMatchObject({ method: 'DELETE', path: '/v1/memories/m-1', body_bytes: 0 });
    expect(body.forwarded).toEqual(expect.arrayContaining(['x-web-service-secret', 'cookie']));
  });

  test('refuses a cross-origin or origin-less DELETE', async ({ request }) => {
    // The framework check and the proxy own check both refuse; either way it never reaches the API.
    for (const headers of [{ origin: 'https://untrusted.example' }, {}] as Record<string, string>[]) {
      const response = await request.delete('/api/v1/memories/m-1', { headers });
      expect(response.status()).toBe(403);
    }
  });

  test('still refuses methods the UI never uses', async ({ request }) => {
    for (const method of ['put', 'patch'] as const) {
      const response = await request[method]('/api/v1/memories/m-1', { headers: { origin: ORIGIN }, data: {} });
      expect(response.status()).toBe(405);
    }
  });
});
