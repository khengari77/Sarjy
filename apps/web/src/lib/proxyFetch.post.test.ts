import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ProxyFetchRequest } from './proxyFetch';

const connectionError = () =>
  Object.assign(new TypeError('fetch failed'), {
    cause: Object.assign(new Error('connect ECONNRESET'), { code: 'ECONNRESET' }),
  });
const answer = (status: number) => new Response(null, { status });
const body = new TextEncoder().encode('{}').buffer;
const noStatusDelay = { statusRetryDelayMs: () => 0 };

function postRequest(path: string): ProxyFetchRequest {
  return {
    url: new URL(path, 'http://api.test'),
    method: 'POST',
    headers: new Headers({ 'content-type': 'application/json', 'x-web-service-secret': 'web-secret' }),
    body,
    timeoutMs: 15_000,
  };
}

function stubFetch(...attempts: Array<() => Promise<Response>>) {
  const fetch = vi.fn();
  for (const attempt of attempts) fetch.mockImplementationOnce(attempt);
  vi.stubGlobal('fetch', fetch);
  return fetch;
}

let proxyFetch: (typeof import('./proxyFetch'))['proxyFetch'];

beforeEach(async () => {
  // A fresh module per test removes the module-level rate-limit state.
  vi.resetModules();
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  ({ proxyFetch } = await import('./proxyFetch'));
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('idempotent-by-state POSTs', () => {
  it.each([
    ['/v1/memories/abc/hide', 'connection error'],
    ['/v1/memories/abc/hide', '503'],
    ['/v1/memories/abc/unhide', 'connection error'],
    ['/v1/memories/abc/unhide', '503'],
    ['/v1/voice/sessions/abc/end', 'connection error'],
    ['/v1/voice/sessions/abc/end', '503'],
    ['/v1/voice/sessions/abc/intro-complete', 'connection error'],
    ['/v1/voice/sessions/abc/intro-complete', '503'],
    ['/v1/changes/abc/undo', 'connection error'],
    ['/v1/changes/abc/undo', '503'],
  ] as const)('retries POST %s once after a %s', async (path, failure) => {
    const fetch = stubFetch(
      failure === '503' ? () => Promise.resolve(answer(503)) : () => Promise.reject(connectionError()),
      () => Promise.resolve(answer(200)),
    );
    const response = await proxyFetch(postRequest(path), noStatusDelay);
    expect(response.status).toBe(200);
    expect(fetch).toHaveBeenCalledTimes(2);
    const firstInit = fetch.mock.calls[0]![1] as RequestInit;
    const retryInit = fetch.mock.calls[1]![1] as RequestInit;
    expect(new Uint8Array(retryInit.body as ArrayBuffer)).toEqual(new Uint8Array(firstInit.body as ArrayBuffer));
    expect(retryInit.signal).not.toBe(firstInit.signal);
  });

  it('matches the pathname only and ignores the query string', async () => {
    const fetch = stubFetch(
      () => Promise.resolve(answer(503)),
      () => Promise.resolve(answer(200)),
    );
    const response = await proxyFetch(postRequest('/v1/memories/abc/unhide?token=secret'), noStatusDelay);
    expect(response.status).toBe(200);
    expect(fetch).toHaveBeenCalledTimes(2);
  });
});

describe('POSTs outside the allow-list', () => {
  it.each([
    '/v1/memories/abc/hide/extra',
    '/v1/memories/abc/hide/',
    '/v1/memories/abc%2Fhide',
    '/v1/memories/abc/correct',
    '/v1/memories/abc/delete',
    '/v1/conversations',
    '/v1/voice/sessions',
  ])('does not retry POST %s after a connection error', async (path) => {
    const fetch = stubFetch(() => Promise.reject(connectionError()));
    await expect(proxyFetch(postRequest(path))).rejects.toThrow();
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('does not match a dot segment, which URL normalization removes before matching', async () => {
    const fetch = stubFetch(() => Promise.reject(connectionError()));
    const target = postRequest('/v1/memories/../hide');
    expect(target.url.pathname).toBe('/v1/hide');
    await expect(proxyFetch(target)).rejects.toThrow();
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
