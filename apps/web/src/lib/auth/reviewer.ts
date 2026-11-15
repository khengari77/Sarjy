/**
 * Reviewer invitations are an additional door beside the shared access token.
 *
 * The API owns the authoritative reviewer session: invitations are revocable
 * and capped, so a browser cookie cannot be validated from configuration at the
 * web edge. This module only decides which web paths must stay open while that
 * cookie is exchanged; every `/v1/*` request is still authorised by the API.
 */
export const REVIEWER_COOKIE = 'sarjy_demo';

/** Strict switch: only the literal `true` enables reviewer mode. */
export function isReviewerMode(value: string | undefined | null): boolean {
  return value === 'true';
}

/**
 * `/review` carries the invitation fragment and the exchange endpoint sets the
 * reviewer cookie. Both must be reachable before the visitor holds any session.
 */
export function isReviewerPublicPath(pathname: string): boolean {
  if (pathname === '/review' || pathname.startsWith('/review/')) return true;
  return pathname === '/api/v1/reviewer/session';
}
