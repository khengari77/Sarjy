import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('$app/env/private', () => ({ API_INTERNAL_URL: 'http://api.test', WEB_SERVICE_SECRET: 'web-secret' }));

/** Minimal request event for the route under test. */
function event(path = 'v1/memories', init: RequestInit = {}) {
  return {
    request: new Request('http://web.test/api/' + path, { method: 'GET', ...init }),
    params: { path },
    url: new URL('http://web.test/api/' + path),
    getClientAddress: () => '203.0.113.7',
  } as unknown as Parameters<typeof import('./+server').GET>[0];
}

let GET: (typeof import('./+server'))['GET'];

beforeEach(async () => {
  vi.resetModules();
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  ({ GET } = await import('./+server'));
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('api proxy route', () => {
  it('returns the 503 unavailable body when the GET times out (no retry)', async () => {
    const fetch = vi.fn(() => Promise.reject(new DOMException('timed out', 'TimeoutError')));
    vi.stubGlobal('fetch', fetch);
    const response = await GET(event());
    expect(response.status).toBe(503);
    await expect(response.json()).resolves.toEqual({ detail: { code: 'API_UNAVAILABLE' } });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('passes a successful response through with no-store', async () => {
    const fetch = vi.fn(() => Promise.resolve(Response.json({ ok: true })));
    vi.stubGlobal('fetch', fetch);
    const response = await GET(event());
    expect(response.status).toBe(200);
    expect(response.headers.get('cache-control')).toBe('no-store');
    await expect(response.json()).resolves.toEqual({ ok: true });
  });
});
