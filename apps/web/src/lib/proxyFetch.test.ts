import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ProxyFetchRequest } from './proxyFetch';

const SECRET = 'web-secret';
const COOKIE = `sarjy_account=${'a'.repeat(43)}`;
const UUID = '123e4567-e89b-12d3-a456-426614174000';
const LEAK_MESSAGE = `connect ECONNRESET https://api.internal.example/v1/conversations/${UUID}?token=${SECRET} (${COOKIE})`;

const connectionError = () =>
  Object.assign(new TypeError(`fetch failed: ${LEAK_MESSAGE}`), {
    cause: Object.assign(new Error(LEAK_MESSAGE), { code: 'ECONNRESET' }),
  });

const answer = (status: number) => new Response(null, { status });

function answerWithBody(status: number) {
  const response = new Response('discard', { status });
  const cancel = vi.spyOn(response.body!, 'cancel');
  return { response, cancel };
}

function request(overrides: Partial<ProxyFetchRequest> = {}): ProxyFetchRequest {
  return {
    url: new URL(`/v1/conversations/${UUID}/memories?token=${SECRET}`, 'http://api.test'),
    method: 'GET',
    headers: new Headers({ 'x-web-service-secret': SECRET, cookie: COOKIE }),
    timeoutMs: 15_000,
    ...overrides,
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
  vi.useRealTimers();
});

describe('retryable requests', () => {
  it('retries GET once after a connection error, then returns the response', async () => {
    const fetch = stubFetch(
      () => Promise.reject(connectionError()),
      () => Promise.resolve(answer(200)),
    );
    const response = await proxyFetch(request());
    expect(response.status).toBe(200);
    expect(fetch).toHaveBeenCalledTimes(2);
    const [firstUrl, firstInit] = fetch.mock.calls[0]!;
    const [retryUrl, retryInit] = fetch.mock.calls[1]!;
    expect(retryUrl).toEqual(firstUrl);
    expect(retryInit.method).toBe(firstInit.method);
    expect(retryInit.headers).toEqual(firstInit.headers);
    // A fresh deadline per attempt; the first signal must not be reused.
    expect(retryInit.signal).not.toBe(firstInit.signal);
    expect(retryInit.signal).toBeInstanceOf(AbortSignal);
  });

  it('retries GET once after a 503 and cancels the discarded body', async () => {
    const first = answerWithBody(503);
    const fetch = stubFetch(
      () => Promise.resolve(first.response),
      () => Promise.resolve(answer(200)),
    );
    const response = await proxyFetch(request());
    expect(response.status).toBe(200);
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(first.cancel).toHaveBeenCalledTimes(1);
  });

  it.each([502, 503, 504])('retries GET once after an ingress %s', async (status) => {
    stubFetch(
      () => Promise.resolve(answer(status)),
      () => Promise.resolve(answer(200)),
    );
    await expect(proxyFetch(request())).resolves.toHaveProperty('status', 200);
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2);
  });

  it('retries DELETE once after a connection error', async () => {
    const fetch = stubFetch(
      () => Promise.reject(connectionError()),
      () => Promise.resolve(answer(200)),
    );
    const response = await proxyFetch(request({ method: 'DELETE' }));
    expect(response.status).toBe(200);
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it('retries POST with an idempotency key and resends the same body', async () => {
    const body = new TextEncoder().encode(JSON.stringify({ memory_ids: ['memory-1'] })).buffer;
    const headers = new Headers({
      'content-type': 'application/json',
      'idempotency-key': 'confirm-1',
      'x-web-service-secret': SECRET,
    });
    const fetch = stubFetch(
      () => Promise.reject(connectionError()),
      () => Promise.resolve(answer(200)),
    );
    const response = await proxyFetch(request({ method: 'POST', headers, body }));
    expect(response.status).toBe(200);
    expect(fetch).toHaveBeenCalledTimes(2);
    const firstInit = fetch.mock.calls[0]![1] as RequestInit;
    const retryInit = fetch.mock.calls[1]![1] as RequestInit;
    expect(new Uint8Array(retryInit.body as ArrayBuffer)).toEqual(new Uint8Array(firstInit.body as ArrayBuffer));
  });
});

describe('non-retryable requests', () => {
  it('does not retry a GET timeout', async () => {
    const fetch = stubFetch(() => Promise.reject(new DOMException('timed out', 'TimeoutError')));
    await expect(proxyFetch(request())).rejects.toHaveProperty('name', 'TimeoutError');
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('does not retry POST without an idempotency key, even on a connection error', async () => {
    const fetch = stubFetch(() => Promise.reject(connectionError()));
    await expect(proxyFetch(request({ method: 'POST', headers: new Headers() }))).rejects.toHaveProperty(
      'name',
      'TypeError',
    );
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('does not retry POST without an idempotency key after a 503', async () => {
    const fetch = stubFetch(() => Promise.resolve(answer(503)));
    const response = await proxyFetch(request({ method: 'POST', headers: new Headers() }));
    expect(response.status).toBe(503);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it.each([500, 404])('does not retry GET after %s', async (status) => {
    const fetch = stubFetch(() => Promise.resolve(answer(status)));
    const response = await proxyFetch(request());
    expect(response.status).toBe(status);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(vi.mocked(console.warn)).not.toHaveBeenCalled();
  });
});

describe('proxy failure diagnostics', () => {
  it('logs one line with the exact key set and a normalized path', async () => {
    const warn = vi.mocked(console.warn);
    stubFetch(
      () => Promise.reject(connectionError()),
      () => Promise.reject(connectionError()),
    );
    await expect(proxyFetch(request())).rejects.toThrow();
    expect(warn).toHaveBeenCalledTimes(1);
    expect(warn).toHaveBeenCalledWith(
      '[Sarjy] api proxy failed',
      expect.objectContaining({
        method: 'GET',
        path: '/v1/conversations/:id/memories',
        reason: 'network',
        name: 'TypeError',
        code: 'ECONNRESET',
        attempts: 2,
        ms: expect.any(Number),
      }),
    );
    const logged = warn.mock.calls[0]![1] as Record<string, unknown>;
    expect(Object.keys(logged).sort()).toEqual(['attempts', 'code', 'method', 'ms', 'name', 'path', 'reason']);
    expect(logged.path).toBe('/v1/conversations/:id/memories');
  });

  it('logs a status failure after the retry with its status', async () => {
    const warn = vi.mocked(console.warn);
    stubFetch(
      () => Promise.resolve(answer(503)),
      () => Promise.resolve(answer(503)),
    );
    const response = await proxyFetch(request());
    expect(response.status).toBe(503);
    expect(warn).toHaveBeenCalledTimes(1);
    const logged = warn.mock.calls[0]![1] as Record<string, unknown>;
    expect(logged).toMatchObject({ method: 'GET', reason: 'status', status: 503, attempts: 2 });
    expect(Object.keys(logged).sort()).toEqual(['attempts', 'method', 'ms', 'path', 'reason', 'status']);
  });

  it('logs a timeout as a single attempt', async () => {
    const warn = vi.mocked(console.warn);
    stubFetch(() => Promise.reject(new DOMException('timed out', 'TimeoutError')));
    await expect(proxyFetch(request())).rejects.toThrow();
    const logged = warn.mock.calls[0]![1] as Record<string, unknown>;
    expect(logged).toMatchObject({ reason: 'timeout', name: 'TimeoutError', attempts: 1 });
    expect(Object.keys(logged).sort()).toEqual(['attempts', 'method', 'ms', 'name', 'path', 'reason']);
  });

  it('never logs the query string, secret, cookie or error message', async () => {
    const warn = vi.mocked(console.warn);
    stubFetch(
      () => Promise.reject(connectionError()),
      () => Promise.reject(connectionError()),
    );
    await expect(proxyFetch(request())).rejects.toThrow();
    const serialized = JSON.stringify(warn.mock.calls);
    expect(serialized).not.toContain(SECRET);
    expect(serialized).not.toContain(COOKIE);
    expect(serialized).not.toContain('token=');
    expect(serialized).not.toContain('api.internal.example');
    expect(serialized).not.toContain(LEAK_MESSAGE);
    expect(serialized).not.toContain('connect ECONNRESET');
    expect(serialized).not.toContain('fetch failed');
  });

  it('logs at most once per ten seconds and reports the suppressed failures', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2030-01-01T00:00:00Z'));
    const warn = vi.mocked(console.warn);
    stubFetch(
      () => Promise.resolve(answer(503)),
      () => Promise.resolve(answer(503)),
      () => Promise.resolve(answer(503)),
      () => Promise.resolve(answer(503)),
    );
    await proxyFetch(request());
    await proxyFetch(request());
    expect(warn).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(10_000);
    stubFetch(
      () => Promise.resolve(answer(503)),
      () => Promise.resolve(answer(503)),
    );
    await proxyFetch(request());
    expect(warn).toHaveBeenCalledTimes(2);
    const logged = warn.mock.calls[1]![1] as Record<string, unknown>;
    expect(logged).toMatchObject({ reason: 'status', status: 503, suppressed: 1 });
    expect(Object.keys(logged).sort()).toEqual(['attempts', 'method', 'ms', 'path', 'reason', 'status', 'suppressed']);
  });
});
