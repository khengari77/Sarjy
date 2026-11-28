import type { Cookies } from '@sveltejs/kit';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ACCOUNT_COOKIE } from './account';
import { accountSession } from './account.server';

vi.mock('$app/env/private', () => ({ API_INTERNAL_URL: 'http://api.test', WEB_SERVICE_SECRET: 'web-secret' }));

const TOKEN = 'a'.repeat(43);
const CONNECTION_CODE = 'ECONNREFUSED';
const cookies = {
  get: (name: string) => (name === ACCOUNT_COOKIE ? TOKEN : undefined),
} as unknown as Cookies;

const connectionError = () => Object.assign(new TypeError('fetch failed'), { cause: { code: CONNECTION_CODE } });
const answer = (status: number) => new Response(null, { status });

function answerWithBody(status: number) {
  const response = new Response('discard', { status });
  const cancel = vi.spyOn(response.body!, 'cancel');
  return { response, cancel };
}

function stubFetch(...attempts: Array<() => Promise<Response>>) {
  const fetch = vi.fn();
  for (const attempt of attempts) fetch.mockImplementationOnce(attempt);
  vi.stubGlobal('fetch', fetch);
  return fetch;
}

beforeEach(() => {
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('account session verification', () => {
  it('retries once when the connection fails, then reports the session valid', async () => {
    const fetch = stubFetch(
      () => Promise.reject(connectionError()),
      () => Promise.resolve(answer(200)),
    );
    await expect(accountSession(cookies)).resolves.toBe('valid');
    expect(fetch).toHaveBeenCalledTimes(2);
    const [firstUrl, firstInit] = fetch.mock.calls[0]!;
    const [retryUrl, retryInit] = fetch.mock.calls[1]!;
    expect(retryUrl).toEqual(firstUrl);
    expect(retryInit.headers).toEqual(firstInit.headers);
    expect(retryInit.headers.cookie).toBe(`${ACCOUNT_COOKIE}=${TOKEN}`);
    expect(retryInit.headers).not.toHaveProperty('connection');
    expect(retryInit.method).toBe(firstInit.method);
    expect(retryInit.body).toBe(firstInit.body);
  });

  it('retries once when the connection fails, then reports the session absent on 401', async () => {
    const fetch = stubFetch(
      () => Promise.reject(connectionError()),
      () => Promise.resolve(answer(401)),
    );
    await expect(accountSession(cookies)).resolves.toBe('none');
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it.each([502, 503, 504])('retries once after an ingress %s, then reports the session valid', async (status) => {
    const first = answerWithBody(status);
    const fetch = stubFetch(
      () => Promise.resolve(first.response),
      () => Promise.resolve(answer(200)),
    );
    await expect(accountSession(cookies)).resolves.toBe('valid');
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(first.cancel).toHaveBeenCalledTimes(1);
  });

  it('reports the service unavailable after two connection failures', async () => {
    const fetch = stubFetch(
      () => Promise.reject(connectionError()),
      () => Promise.reject(connectionError()),
    );
    await expect(accountSession(cookies)).resolves.toBe('unavailable');
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it('does not retry a timeout', async () => {
    const fetch = stubFetch(() => Promise.reject(new DOMException('timed out', 'TimeoutError')));
    await expect(accountSession(cookies)).resolves.toBe('unavailable');
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('does not retry a plain 401', async () => {
    const fetch = stubFetch(() => Promise.resolve(answer(401)));
    await expect(accountSession(cookies)).resolves.toBe('none');
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('does not retry a 500', async () => {
    const fetch = stubFetch(() => Promise.resolve(answer(500)));
    await expect(accountSession(cookies)).resolves.toBe('unavailable');
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});

describe('unavailable diagnostics', () => {
  // Each test starts more than one rate-limit window after the previous one, so
  // the first failure in every test is always logged.
  let clock = Date.now();

  beforeEach(() => {
    clock += 60_000;
    vi.useFakeTimers();
    vi.setSystemTime(clock);
  });

  it('logs the network cause once after both attempts', async () => {
    const warn = vi.mocked(console.warn);
    stubFetch(
      () => Promise.reject(connectionError()),
      () => Promise.reject(connectionError()),
    );
    await expect(accountSession(cookies)).resolves.toBe('unavailable');
    expect(warn).toHaveBeenCalledTimes(1);
    expect(warn).toHaveBeenCalledWith(
      '[Sarjy] account session check unavailable',
      expect.objectContaining({
        reason: 'network',
        name: 'TypeError',
        code: CONNECTION_CODE,
        attempts: 2,
        ms: expect.any(Number),
      }),
    );
    const logged = warn.mock.calls[0]![1] as Record<string, unknown>;
    expect(logged).not.toHaveProperty('status');
  });

  it('logs a timeout as a single attempt', async () => {
    const warn = vi.mocked(console.warn);
    stubFetch(() => Promise.reject(new DOMException('timed out', 'TimeoutError')));
    await expect(accountSession(cookies)).resolves.toBe('unavailable');
    expect(warn).toHaveBeenCalledTimes(1);
    expect(warn).toHaveBeenCalledWith(
      '[Sarjy] account session check unavailable',
      expect.objectContaining({ reason: 'timeout', name: 'TimeoutError', attempts: 1, ms: expect.any(Number) }),
    );
    expect(warn.mock.calls[0]![1]).not.toHaveProperty('status');
  });

  it('logs a non-ok status with its code', async () => {
    const warn = vi.mocked(console.warn);
    stubFetch(() => Promise.resolve(answer(500)));
    await expect(accountSession(cookies)).resolves.toBe('unavailable');
    expect(warn).toHaveBeenCalledTimes(1);
    expect(warn).toHaveBeenCalledWith(
      '[Sarjy] account session check unavailable',
      expect.objectContaining({ reason: 'status', status: 500, attempts: 1, ms: expect.any(Number) }),
    );
    const logged = warn.mock.calls[0]![1] as Record<string, unknown>;
    expect(logged).not.toHaveProperty('name');
    expect(logged).not.toHaveProperty('code');
  });

  it.each<[number, 'valid' | 'none']>([
    [200, 'valid'],
    [401, 'none'],
  ])('logs nothing for HTTP %s', async (status, result) => {
    const warn = vi.mocked(console.warn);
    stubFetch(() => Promise.resolve(answer(status)));
    await expect(accountSession(cookies)).resolves.toBe(result);
    expect(warn).not.toHaveBeenCalled();
  });

  it('never logs the cookie value or the service secret', async () => {
    const warn = vi.mocked(console.warn);
    stubFetch(
      () => Promise.reject(connectionError()),
      () => Promise.reject(connectionError()),
    );
    await accountSession(cookies);
    const serialized = JSON.stringify(warn.mock.calls);
    expect(serialized).not.toContain(TOKEN);
    expect(serialized).not.toContain('web-secret');
  });

  it('logs at most once per ten seconds and reports the suppressed failures', async () => {
    const warn = vi.mocked(console.warn);
    stubFetch(
      () => Promise.resolve(answer(500)),
      () => Promise.resolve(answer(500)),
    );
    await accountSession(cookies);
    await accountSession(cookies);
    expect(warn).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(10_000);
    stubFetch(() => Promise.resolve(answer(500)));
    await accountSession(cookies);

    expect(warn).toHaveBeenCalledTimes(2);
    expect(warn.mock.calls[1]![1]).toMatchObject({ reason: 'status', status: 500, suppressed: 1 });
  });
});
