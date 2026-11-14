import type { Handle } from '@sveltejs/kit/hooks';
import { assertAccessConfigured, protectedMode, sessionIsValid } from './lib/auth/guard.server';

assertAccessConfigured();

const PUBLIC_PATHS = new Set(['/healthz', '/logout', '/favicon.ico', '/favicon.svg', '/favicon.png', '/robots.txt']);
const PUBLIC_PREFIXES = ['/_app/', '/@vite/', '/@fs/', '/@id/', '/node_modules/', '/src/', '/.svelte-kit/'];

function isPublic(pathname: string): boolean {
  return PUBLIC_PATHS.has(pathname) || PUBLIC_PREFIXES.some((prefix) => pathname.startsWith(prefix));
}

function isPageRequest(request: Request): boolean {
  if (request.method !== 'GET' && request.method !== 'HEAD') return false;
  return (request.headers.get('accept') ?? '').includes('text/html');
}

function secure(response: Response, entry = false): Response {
  response.headers.set('referrer-policy', 'no-referrer');
  const type = response.headers.get('content-type') ?? '';
  if (entry || type.includes('text/html')) response.headers.set('cache-control', 'no-store');
  return response;
}

export const handle: Handle = async ({ event, resolve }) => {
  // Unprotected development is unchanged: no gate, no redirects, no headers.
  if (!protectedMode) return resolve(event);

  const { pathname, search } = event.url;
  if (isPublic(pathname)) return secure(await resolve(event));

  if (sessionIsValid(event.cookies)) return secure(await resolve(event));

  // The root is the door: it renders the token field, serves the `?token=`
  // exchange and accepts the no-JavaScript form post.
  if (pathname === '/') return secure(await resolve(event), true);

  if (isPageRequest(event.request)) {
    const next = `${pathname}${search}`;
    return new Response(null, {
      status: 303,
      headers: {
        location: `/?next=${encodeURIComponent(next)}`,
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
