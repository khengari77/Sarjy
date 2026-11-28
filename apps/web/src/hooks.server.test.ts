import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  sessionIsValid: vi.fn(),
  reviewerSessionPresent: vi.fn(),
  accountSession: vi.fn(),
}));

vi.mock('./lib/auth/guard.server', () => ({
  assertAccessConfigured: vi.fn(),
  protectedMode: true,
  accountMode: false,
  reviewerMode: false,
  reviewerSessionPresent: mocks.reviewerSessionPresent,
  sessionIsValid: mocks.sessionIsValid,
}));

vi.mock('./lib/auth/account.server', () => ({ accountSession: mocks.accountSession }));
vi.mock('./lib/auth/account', () => ({ isAccountPublicPath: () => false }));
vi.mock('./lib/auth/reviewer', () => ({ isReviewerPublicPath: () => false }));

const { handle } = await import('./hooks.server');

function event(pathname: string) {
  return {
    url: new URL(`http://localhost${pathname}`),
    cookies: {},
    request: new Request(`http://localhost${pathname}`, { headers: { accept: 'audio/mpeg, */*' } }),
    isDataRequest: false,
  };
}

async function run(pathname: string, response = new Response('clip', { status: 200 })) {
  return handle({
    event: event(pathname) as never,
    resolve: (async () => response) as never,
  });
}

beforeEach(() => {
  mocks.sessionIsValid.mockReset();
  mocks.reviewerSessionPresent.mockReset();
});

describe('greeting clip gate and cache headers', () => {
  it('lets a signed-in request through with the private greeting cache', async () => {
    mocks.sessionIsValid.mockReturnValue(true);
    const response = await run('/greetings/aura-2-thalia-en/full.mp3');
    expect(response.status).toBe(200);
    expect(response.headers.get('cache-control')).toBe('private, max-age=86400');
  });

  it('refuses an unauthenticated greeting request without the clip cache header', async () => {
    mocks.sessionIsValid.mockReturnValue(false);
    const response = await run('/greetings/aura-2-thalia-en/full.mp3');
    expect(response.status).toBe(401);
    expect(response.headers.get('cache-control')).not.toBe('private, max-age=86400');
  });

  it('adds no greeting cache header to other paths', async () => {
    mocks.sessionIsValid.mockReturnValue(true);
    const response = await run('/api/v1/settings');
    expect(response.headers.get('cache-control')).not.toBe('private, max-age=86400');
  });
});
