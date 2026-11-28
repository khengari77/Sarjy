/** Browser-only caching: clips sit behind the protected gate, so never share them. */
export const GREETING_CACHE_CONTROL = 'private, max-age=86400';

export function applyGreetingCacheHeaders(pathname: string, response: Response): Response {
  if (pathname.startsWith('/greetings/')) response.headers.set('cache-control', GREETING_CACHE_CONTROL);
  return response;
}
