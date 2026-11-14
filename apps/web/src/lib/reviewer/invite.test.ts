import { describe, expect, it, vi } from 'vitest';
import { INVITATION_FRAGMENT_PREFIX, openInvitation, readInvitationToken } from './invite';

const TOKEN = 'A'.repeat(43);
const HASH = `${INVITATION_FRAGMENT_PREFIX}${TOKEN}`;

function harness(status: number) {
  const fetcher = vi.fn(async () => new Response(JSON.stringify({ id: 'owner' }), { status }));
  const replaceState = vi.fn();
  const onBoot = vi.fn();
  return {
    fetcher: fetcher as unknown as typeof fetch,
    history: { replaceState },
    onBoot,
    replaceState,
  };
}

describe('reviewer invitation link', () => {
  it('reads only a well-formed fragment token', () => {
    expect(readInvitationToken(HASH)).toBe(TOKEN);
    expect(readInvitationToken(`?invitation=${TOKEN}`)).toBeNull();
    expect(readInvitationToken(`${INVITATION_FRAGMENT_PREFIX}short`)).toBeNull();
  });

  it('valid token exchanges, clears the fragment and boots', async () => {
    const h = harness(200);
    const result = await openInvitation(HASH, {
      fetcher: h.fetcher,
      history: h.history,
      pathname: '/review',
      onBoot: h.onBoot,
    });
    expect(result).toBe('ok');
    // The fragment is replaced before the network call, so the token never
    // reaches history, the address bar, logs or Referer.
    expect(h.replaceState).toHaveBeenCalledWith(null, '', '/review');
    expect(h.replaceState.mock.invocationCallOrder[0]).toBeLessThan(
      (h.fetcher as unknown as { mock: { invocationCallOrder: number[] } }).mock.invocationCallOrder[0],
    );
    expect(h.fetcher).toHaveBeenCalledWith(
      '/api/v1/reviewer/session',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ invitation_token: TOKEN }) }),
    );
    expect(h.onBoot).toHaveBeenCalledTimes(1);
  });

  it('revoked token shows the generic failure without booting', async () => {
    const h = harness(403);
    const result = await openInvitation(HASH, {
      fetcher: h.fetcher,
      history: h.history,
      pathname: '/review',
      onBoot: h.onBoot,
    });
    expect(result).toBe('invalid');
    expect(h.replaceState).toHaveBeenCalledWith(null, '', '/review');
    expect(h.onBoot).not.toHaveBeenCalled();
  });

  it('expired, revoked and exhausted all look identical', async () => {
    for (const status of [401, 403, 429]) {
      const h = harness(status);
      expect(
        await openInvitation(HASH, {
          fetcher: h.fetcher,
          history: h.history,
          pathname: '/review',
          onBoot: h.onBoot,
        }),
      ).toBe('invalid');
      expect(h.onBoot).not.toHaveBeenCalled();
    }
  });
});
