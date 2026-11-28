import type { Cookies } from '@sveltejs/kit';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ACCOUNT_COOKIE } from './account';

vi.mock('$app/env/private', () => ({ API_INTERNAL_URL: 'http://api.test', WEB_SERVICE_SECRET: 'web-secret' }));

const TOKEN = 'a'.repeat(43);
const CONNECTION_CODE = 'ECONNRESET';
const LEAK_URL = 'https://api.internal.example/v1/auth/session?x=1';
const LEAK_HEADER = 'authorization: Bearer header-value';
const LEAK_MESSAGE = `connect ECONNRESET ${LEAK_URL} (${LEAK_HEADER})`;
const cookies = {
  get: (name: string) => (name === ACCOUNT_COOKIE ? TOKEN : undefined),
} as unknown as Cookies;

const connectionError = () =>
  Object.assign(new TypeError(`fetch failed: ${LEAK_URL} (${LEAK_HEADER})`), {
    cause: Object.assign(new Error(LEAK_MESSAGE), { code: CONNECTION_CODE }),
  });
const answer = (status: number) => new Response(null, { status });

function answerWithBody(status: number) {
  const response = new Response('discard', { status });
  const cancel = vi.spyOn(response.body!, 'cancel');
  return { response, cancel };
}

function answerWithFailingCancel(status: number) {
  const response = new Response('discard', { status });
  vi.spyOn(response.body!, 'cancel').mockRejectedValue(new Error('cannot cancel'));
  return response;
}

function errorWithThrowing(getter: 'name' | 'cause') {
  const error = new TypeError('fetch failed');
  Object.defineProperty(error, getter, {
    get() {
      throw new Error(`${getter} getter failed`);
    },
  });
  return error;
}

function stubFetch(...attempts: Array<() => Promise<Response>>) {
  const fetch = vi.fn();
  for (const attempt of attempts) fetch.mockImplementationOnce(attempt);
  vi.stubGlobal('fetch', fetch);
  return fetch;
}

let accountSession: (typeof import('./account.server'))['accountSession'];

beforeEach(async () => {
  // A fresh module per test removes the module-level rate-limit state.
  vi.resetModules();
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  ({ accountSession } = await import('./account.server'));
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
  beforeEach(() => {
    // A fixed base for each test is safe because the module state is reset above.
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2030-01-01T00:00:00Z'));
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
    expect(Object.keys(logged).sort()).toEqual(['attempts', 'code', 'ms', 'name', 'reason']);
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
    const logged = warn.mock.calls[0]![1] as Record<string, unknown>;
    expect(Object.keys(logged).sort()).toEqual(['attempts', 'ms', 'name', 'reason']);
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
    expect(Object.keys(logged).sort()).toEqual(['attempts', 'ms', 'reason', 'status']);
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

  it.each<[number, 'valid' | 'none']>([
    [200, 'valid'],
    [401, 'none'],
  ])('ignores a body-cancel failure for HTTP %s', async (status, result) => {
    const warn = vi.mocked(console.warn);
    stubFetch(() => Promise.resolve(answerWithFailingCancel(status)));
    await expect(accountSession(cookies)).resolves.toBe(result);
    expect(warn).not.toHaveBeenCalled();
  });

  it('never logs the URL, error messages, headers, cookie value or service secret', async () => {
    const warn = vi.mocked(console.warn);
    stubFetch(
      () => Promise.reject(connectionError()),
      () => Promise.reject(connectionError()),
    );
    await accountSession(cookies);
    const serialized = JSON.stringify(warn.mock.calls);
    expect(serialized).not.toContain(LEAK_URL);
    expect(serialized).not.toContain(LEAK_MESSAGE);
    expect(serialized).not.toContain('connect ECONNRESET');
    expect(serialized).not.toContain(LEAK_HEADER);
    expect(serialized).not.toContain('fetch failed');
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
    const logged = warn.mock.calls[1]![1] as Record<string, unknown>;
    expect(logged).toMatchObject({ reason: 'status', status: 500, suppressed: 1 });
    expect(Object.keys(logged).sort()).toEqual(['attempts', 'ms', 'reason', 'status', 'suppressed']);
  });
});

describe('diagnostic extraction robustness', () => {
  it('stays unavailable when the name getter throws', async () => {
    const warn = vi.mocked(console.warn);
    stubFetch(
      () => Promise.reject(errorWithThrowing('name')),
      () => Promise.reject(errorWithThrowing('name')),
    );
    await expect(accountSession(cookies)).resolves.toBe('unavailable');
    expect(warn).toHaveBeenCalledTimes(1);
    const logged = warn.mock.calls[0]![1] as Record<string, unknown>;
    expect(Object.keys(logged).sort()).toEqual(['attempts', 'ms', 'reason']);
    expect(logged.reason).toBe('network');
    expect(logged.attempts).toBe(2);
  });

  it('stays unavailable when the cause getter throws', async () => {
    const warn = vi.mocked(console.warn);
    stubFetch(
      () => Promise.reject(errorWithThrowing('cause')),
      () => Promise.reject(errorWithThrowing('cause')),
    );
    await expect(accountSession(cookies)).resolves.toBe('unavailable');
    expect(warn).toHaveBeenCalledTimes(1);
    const logged = warn.mock.calls[0]![1] as Record<string, unknown>;
    expect(Object.keys(logged).sort()).toEqual(['attempts', 'ms', 'name', 'reason']);
    expect(logged.name).toBe('TypeError');
    expect(logged).not.toHaveProperty('code');
  });
});
