/** Greeting clips are immutable per voice/kind; let the browser reuse them. */
export const GREETING_CACHE_CONTROL = 'public, max-age=86400';

export function applyGreetingCacheHeaders(pathname: string, response: Response): Response {
  if (pathname.startsWith('/greetings/')) response.headers.set('cache-control', GREETING_CACHE_CONTROL);
  return response;
}
