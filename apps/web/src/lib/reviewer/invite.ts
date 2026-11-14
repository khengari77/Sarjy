/**
 * Reviewer invitation link flow.
 *
 * The token travels in the URL fragment, which browsers never send to the
 * server, so it cannot appear in access logs or a `Referer` header. The
 * fragment is replaced before the exchange request so it does not linger in
 * the address bar or back/forward history.
 */
export const INVITATION_FRAGMENT_PREFIX = '#invitation=';
export const INVITATION_TOKEN_PATTERN = /^[A-Za-z0-9_-]{43}$/;

export function readInvitationToken(hash: string): string | null {
  if (!hash.startsWith(INVITATION_FRAGMENT_PREFIX)) return null;
  const token = hash.slice(INVITATION_FRAGMENT_PREFIX.length);
  return INVITATION_TOKEN_PATTERN.test(token) ? token : null;
}

export type ExchangeResult = 'ok' | 'invalid';

export async function exchangeInvitation(fetcher: typeof fetch, token: string): Promise<ExchangeResult> {
  try {
    const response = await fetcher('/api/v1/reviewer/session', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ invitation_token: token }),
    });
    return response.ok ? 'ok' : 'invalid';
  } catch {
    return 'invalid';
  }
}

export type OpenInvitationOptions = {
  fetcher: typeof fetch;
  history: Pick<History, 'replaceState'>;
  pathname: string;
  onBoot: () => void;
};

/**
 * Exchange the fragment token and boot the app. Expired, revoked and exhausted
 * invitations all produce the same `invalid` result so the failure reveals
 * nothing about which one it was.
 */
export async function openInvitation(rawHash: string, options: OpenInvitationOptions): Promise<ExchangeResult> {
  const token = readInvitationToken(rawHash);
  options.history.replaceState(null, '', options.pathname);
  if (token === null) return 'invalid';
  const result = await exchangeInvitation(options.fetcher, token);
  if (result === 'ok') {
    options.onBoot();
    return 'ok';
  }
  return 'invalid';
}
