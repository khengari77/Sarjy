import type { Handle } from '@sveltejs/kit/hooks';
import { assertAccessConfigured, protectedMode, sessionIsValid } from './lib/auth/guard.server';

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
  if (html) response.headers.set('cache-control', 'no-store');
  return response;
}

/** Drop a token-bearing query parameter before it reaches a visible URL. */
function nextPath(pathname: string, search: string): string {
  const params = new URLSearchParams(search);
  params.delete('token');
  const query = params.toString();
  return query ? `${pathname}?${query}` : pathname;
}

export const handle: Handle = async ({ event, resolve }) => {
  // Unprotected development is unchanged: no gate, no redirects, no headers.
  if (!protectedMode) return resolve(event);

  const { pathname, search } = event.url;
  if (isPublic(pathname)) return secure(await resolve(event));

  if (sessionIsValid(event.cookies)) return secure(await resolve(event));

  // The root is the door: GET/HEAD render it, and a native form POST (and only
  // that) may exchange a manually entered token. Other methods are not a door.
  if (pathname === '/') {
    const method = event.request.method;
    if (method === 'GET' || method === 'HEAD' || isFormPost(event.request)) {
      return secure(await resolve(event));
    }
  }

  if (isPageRequest(event.request)) {
    return new Response(null, {
      status: 303,
      headers: {
        location: `/?next=${encodeURIComponent(nextPath(pathname, search))}`,
        'referrer-policy': 'no-referrer',
        'cache-control': 'no-store',
      },
    });
  }

  return new Response('Authentication required', {
    status: 401,
    headers: { 'cache-control': 'no-store', 'referrer-policy': 'no-referrer' },
  });
};
