import type { Cookies } from '@sveltejs/kit';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ACCOUNT_COOKIE } from './account';
import { accountSession } from './account.server';

vi.mock('$app/env/private', () => ({ API_INTERNAL_URL: 'http://api.test', WEB_SERVICE_SECRET: 'web-secret' }));

const TOKEN = 'a'.repeat(43);
const cookies = {
  get: (name: string) => (name === ACCOUNT_COOKIE ? TOKEN : undefined),
} as unknown as Cookies;

const connectionError = () => new TypeError('fetch failed');
const answer = (status: number) => new Response(null, { status });

function stubFetch(...attempts: Array<() => Promise<Response>>) {
  const fetch = vi.fn();
  for (const attempt of attempts) fetch.mockImplementationOnce(attempt);
  vi.stubGlobal('fetch', fetch);
  return fetch;
}

afterEach(() => vi.unstubAllGlobals());

describe('account session verification', () => {
  it('retries once when the connection fails, then reports the session valid', async () => {
    const fetch = stubFetch(
      () => Promise.reject(connectionError()),
      () => Promise.resolve(answer(200)),
    );
    await expect(accountSession(cookies)).resolves.toBe('valid');
    expect(fetch).toHaveBeenCalledTimes(2);
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
    const fetch = stubFetch(
      () => Promise.resolve(answer(status)),
      () => Promise.resolve(answer(200)),
    );
    await expect(accountSession(cookies)).resolves.toBe('valid');
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(fetch.mock.calls[1]?.[1]?.headers).toMatchObject({ connection: 'close' });
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
