import type { Handle } from '@sveltejs/kit/hooks';
import { isAccountPublicPath } from './lib/auth/account';
import { accountSession } from './lib/auth/account.server';
import {
  accountMode,
  assertAccessConfigured,
  protectedMode,
  reviewerMode,
  reviewerSessionPresent,
  sessionIsValid,
} from './lib/auth/guard.server';
import { isReviewerPublicPath } from './lib/auth/reviewer';

// Reviewer invitations are an additive door, not a replacement for the shared
// token. When they are off, the #142 gate below behaves exactly as before.
assertAccessConfigured();

const PUBLIC_PATHS = new Set(['/healthz', '/logout', '/favicon.ico', '/favicon.svg', '/favicon.png', '/robots.txt']);
const PUBLIC_PREFIXES = ['/_app/', '/@vite/', '/@fs/', '/@id/', '/node_modules/', '/src/', '/.svelte-kit/'];
const FORM_TYPES = new Set(['application/x-www-form-urlencoded', 'multipart/form-data']);

function isPublic(pathname: string): boolean {
  return PUBLIC_PATHS.has(pathname) || PUBLIC_PREFIXES.some((prefix) => pathname.startsWith(prefix));
}

function isPageRequest(request: Request): boolean {
  if (request.method !== 'GET' && request.method !== 'HEAD') return false;
  return (request.headers.get('accept') ?? '').includes('text/html');
}

function isFormPost(request: Request): boolean {
  if (request.method !== 'POST') return false;
  const type = (request.headers.get('content-type') ?? '').split(';', 1)[0].trim().toLowerCase();
  return FORM_TYPES.has(type);
}

/**
 * `same-origin` on HTML, `no-referrer` everywhere else.
 *
 * A `no-referrer` document sends `Origin: null` on native form posts, which
 * SvelteKit's production CSRF check rejects. `same-origin` keeps a `?token=`
 * out of third-party `Referer` headers (the actual leak) while preserving the
 * `Origin` needed by the no-JavaScript door and the sign-out form.
 */
function secure(response: Response): Response {
  const type = response.headers.get('content-type') ?? '';
  const html = type.includes('text/html');
  response.headers.set('referrer-policy', html ? 'same-origin' : 'no-referrer');
  // The only owner of no-store for pages, redirects and anything issuing a session:
  // a load that also set it would collide with the form action and answer 500.
  const redirect = response.status >= 300 && response.status < 400 && response.headers.has('location');
  // SvelteKit has already attached cookies when `resolve` returns.
  const issuesCookie = response.headers.has('set-cookie');
  if (html || redirect || issuesCookie) response.headers.set('cache-control', 'no-store');
  return response;
}

/** Drop a token-bearing query parameter before it reaches a visible URL. */
function nextPath(pathname: string, search: string): string {
  const params = new URLSearchParams(search);
  params.delete('token');
  const query = params.toString();
  return query ? `${pathname}?${query}` : pathname;
}

const refused = (status: number, message: string) =>
  new Response(message, { status, headers: { 'cache-control': 'no-store', 'referrer-policy': 'no-referrer' } });

const redirected = (location: string) =>
  new Response(null, {
    status: 303,
    headers: { location, 'referrer-policy': 'no-referrer', 'cache-control': 'no-store' },
  });

/**
 * Accounts: the API authorises every `/v1/*` call itself and denies by default,
 * so only pages are checked here, against the session the API holds.
 */
const accountGate: Handle = async ({ event, resolve }) => {
  const { pathname, search } = event.url;
  if (isPublic(pathname) || isAccountPublicPath(pathname) || pathname.startsWith('/api/')) {
    return secure(await resolve(event));
  }
  const session = await accountSession(event.cookies);
  if (session === 'valid') return secure(await resolve(event));
  // Not knowing is not the same as signed out: keep the cookie and say so.
  if (session === 'unavailable') return refused(503, 'Sarjy is not available right now. Try again shortly.');
  if (!isPageRequest(event.request)) return refused(401, 'Authentication required');
  const next = nextPath(pathname, search);
  return redirected(next === '/' ? '/sign-in' : `/sign-in?next=${encodeURIComponent(next)}`);
};

export const handle: Handle = async ({ event, resolve }) => {
  // Unprotected development is unchanged: no gate, no redirects, no headers.
  if (!protectedMode) return resolve(event);
  if (accountMode) return accountGate({ event, resolve });

  const { pathname, search } = event.url;
  if (isPublic(pathname)) return secure(await resolve(event));

  // Precedence: the signed token session is checked first, then the reviewer
  // identity. Either one opens the shell; the API still authorises every
  // `/v1/*` call from the reviewer cookie, so this edge decision never grants
  // data access on its own.
  if (sessionIsValid(event.cookies)) return secure(await resolve(event));

  if (reviewerMode) {
    // `/review` carries the invitation and the exchange route sets the cookie,
    // so both stay open before the visitor holds any credential.
    if (isReviewerPublicPath(pathname) || reviewerSessionPresent(event.cookies)) {
      return secure(await resolve(event));
    }
  }

  // The root is the door: GET/HEAD render it, and a native form POST (and only
  // that) may exchange a manually entered token. Other methods are not a door.
  if (pathname === '/') {
    const method = event.request.method;
    if (method === 'GET' || method === 'HEAD' || isFormPost(event.request)) {
      return secure(await resolve(event));
    }
  }

  if (isPageRequest(event.request)) {
    return redirected(`/?next=${encodeURIComponent(nextPath(pathname, search))}`);
  }

  return refused(401, 'Authentication required');
};
