import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ProxyFetchRequest } from './proxyFetch';

const UUID = '123e4567-e89b-12d3-a456-426614174000';
const body = new TextEncoder().encode('{}').buffer;

const connectionError = () =>
  Object.assign(new TypeError('fetch failed'), {
    cause: Object.assign(new Error('connect ECONNRESET'), { code: 'ECONNRESET' }),
  });
const answer = (status: number) => new Response(null, { status });

function request(overrides: Partial<ProxyFetchRequest> = {}): ProxyFetchRequest {
  return {
    url: new URL(`/v1/conversations/${UUID}/memories`, 'http://api.test'),
    method: 'GET',
    headers: new Headers(),
    timeoutMs: 15_000,
    ...overrides,
  };
}

function postRequest(path: string, headers = new Headers()): ProxyFetchRequest {
  return request({
    url: new URL(path, 'http://api.test'),
    method: 'POST',
    headers: new Headers({ 'content-type': 'application/json', ...Object.fromEntries(headers) }),
    body,
  });
}

function stubFetch(...attempts: Array<() => Promise<Response>>) {
  const fetch = vi.fn();
  for (const attempt of attempts) fetch.mockImplementationOnce(attempt);
  vi.stubGlobal('fetch', fetch);
  return fetch;
}

let proxyFetch: (typeof import('./proxyFetch'))['proxyFetch'];
let bounds: (typeof import('./proxyFetch'))['STATUS_RETRY_BOUNDS_MS'];

beforeEach(async () => {
  vi.resetModules();
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  ({ proxyFetch, STATUS_RETRY_BOUNDS_MS: bounds } = await import('./proxyFetch'));
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('three attempts', () => {
  it('returns 200 after two 502s and a third success', async () => {
    const fetch = stubFetch(
      () => Promise.resolve(answer(502)),
      () => Promise.resolve(answer(502)),
      () => Promise.resolve(answer(200)),
    );
    const response = await proxyFetch(request(), { statusRetryDelayMs: () => 0 });
    expect(response.status).toBe(200);
    expect(fetch).toHaveBeenCalledTimes(3);
  });

  it('returns the last 502 and logs attempts: 3 when every attempt fails', async () => {
    const warn = vi.mocked(console.warn);
    const fetch = stubFetch(
      () => Promise.resolve(answer(502)),
      () => Promise.resolve(answer(502)),
      () => Promise.resolve(answer(502)),
    );
    const response = await proxyFetch(request(), { statusRetryDelayMs: () => 0 });
    expect(response.status).toBe(502);
    expect(fetch).toHaveBeenCalledTimes(3);
    expect(warn.mock.calls[0]![1]).toMatchObject({ reason: 'status', status: 502, attempts: 3 });
  });

  it('recovers when a thrown connection error is followed by 502 then 200', async () => {
    const fetch = stubFetch(
      () => Promise.reject(connectionError()),
      () => Promise.resolve(answer(502)),
      () => Promise.resolve(answer(200)),
    );
    const response = await proxyFetch(request(), { statusRetryDelayMs: () => 0 });
    expect(response.status).toBe(200);
    expect(fetch).toHaveBeenCalledTimes(3);
  });

  it('retries the third attempt after a connection error instead of immediately', async () => {
    const statusRetryDelayMs = vi.fn((retry: number) => retry - retry);
    const fetch = stubFetch(
      () => Promise.reject(connectionError()),
      () => Promise.reject(connectionError()),
      () => Promise.resolve(answer(200)),
    );
    const response = await proxyFetch(request(), { statusRetryDelayMs });
    expect(response.status).toBe(200);
    expect(fetch).toHaveBeenCalledTimes(3);
    expect(statusRetryDelayMs.mock.calls.map(([retry]) => retry)).toEqual([2]);
  });

  it('uses retry 1 then retry 2 delays in order', async () => {
    const statusRetryDelayMs = vi.fn((retry: number) => retry - retry);
    stubFetch(
      () => Promise.resolve(answer(502)),
      () => Promise.resolve(answer(502)),
      () => Promise.resolve(answer(200)),
    );
    await proxyFetch(request(), { statusRetryDelayMs });
    expect(statusRetryDelayMs.mock.calls.map(([retry]) => retry)).toEqual([1, 2]);
  });

  it('pins the default jitter bounds for the second and third attempts', () => {
    expect(bounds).toEqual({ 1: { min: 150, max: 300 }, 2: { min: 500, max: 900 } });
  });

  it('does not retry a timeout despite the extra budget', async () => {
    const fetch = stubFetch(() => Promise.reject(new DOMException('timed out', 'TimeoutError')));
    await expect(proxyFetch(request())).rejects.toHaveProperty('name', 'TimeoutError');
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('starts the third attempt with the same POST body bytes', async () => {
    const fetch = stubFetch(
      () => Promise.resolve(answer(502)),
      () => Promise.resolve(answer(502)),
      () => Promise.resolve(answer(200)),
    );
    const response = await proxyFetch(postRequest('/v1/memories/abc/hide'), { statusRetryDelayMs: () => 0 });
    expect(response.status).toBe(200);
    expect(fetch).toHaveBeenCalledTimes(3);
    for (const [, init] of fetch.mock.calls) {
      expect(new Uint8Array((init as RequestInit).body as ArrayBuffer)).toEqual(new Uint8Array(body));
    }
  });

  it('keeps a non-allow-listed POST at one attempt even with an Idempotency-Key', async () => {
    const fetch = stubFetch(() => Promise.resolve(answer(502)));
    const requestWithKey = postRequest('/v1/conversations', new Headers({ 'idempotency-key': 'client-supplied' }));
    const response = await proxyFetch(requestWithKey, { statusRetryDelayMs: () => 0 });
    expect(response.status).toBe(502);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});

describe('total retry budget', () => {
  it('does not start a retry with less than 1 s remaining', async () => {
    let now = 0;
    const fetch = stubFetch(() => {
      now = 14_500;
      return Promise.resolve(answer(502));
    });
    const response = await proxyFetch(request(), { statusRetryDelayMs: () => 0, nowMs: () => now });
    expect(response.status).toBe(502);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('shortens the second attempt signal to the remaining budget', async () => {
    let now = 0;
    const timeout = vi.spyOn(AbortSignal, 'timeout');
    stubFetch(
      () => {
        now = 10_000;
        return Promise.resolve(answer(502));
      },
      () => Promise.resolve(answer(200)),
    );
    const response = await proxyFetch(request(), { statusRetryDelayMs: () => 0, nowMs: () => now });
    expect(response.status).toBe(200);
    expect(timeout.mock.calls.map(([ms]) => ms)).toEqual([15_000, 5_000]);
  });

  it('checks the budget again after the jitter wait and keeps total elapsed within timeoutMs', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(0);
    const fetch = stubFetch(() => {
      vi.setSystemTime(9_000);
      return Promise.resolve(answer(502));
    });
    const pending = proxyFetch(request(), { statusRetryDelayMs: () => 5_500 });
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(5_500);
    const response = await pending;
    expect(response.status).toBe(502);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(Date.now()).toBeLessThanOrEqual(15_000);
  });

  it('returns a 503 with its body when the retry wait would leave under 1 s', async () => {
    let now = 0;
    const upstream = new Response('{"detail":"busy"}', { status: 503 });
    const fetch = stubFetch(() => {
      now = 13_800;
      return Promise.resolve(upstream);
    });
    const response = await proxyFetch(request(), { statusRetryDelayMs: () => 300, nowMs: () => now });
    expect(response.status).toBe(503);
    await expect(response.text()).resolves.toBe('{"detail":"busy"}');
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('stops after a connection-error wait that exhausts the budget and logs attempts: 2', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(0);
    const warn = vi.mocked(console.warn);
    const fetch = stubFetch(
      () => Promise.reject(connectionError()),
      () => {
        vi.setSystemTime(14_000);
        return Promise.reject(connectionError());
      },
    );
    const pending = proxyFetch(request(), { statusRetryDelayMs: () => 1_500 });
    const rejection = expect(pending).rejects.toHaveProperty('name', 'TypeError');
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(1_500);
    await rejection;
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(warn.mock.calls[0]![1]).toMatchObject({ reason: 'network', attempts: 2 });
  });
});
