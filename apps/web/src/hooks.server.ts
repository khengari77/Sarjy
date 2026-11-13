import type { Handle } from '@sveltejs/kit/hooks';
import { DEVELOPMENT_PROTECTED, DEVELOPMENT_USER, DEVELOPMENT_PASSWORD, WEB_SERVICE_SECRET } from '$app/env/private';
const protectedMode = DEVELOPMENT_PROTECTED === 'true';
if (protectedMode && (!DEVELOPMENT_USER || !DEVELOPMENT_PASSWORD || !WEB_SERVICE_SECRET))
  throw new Error('DEVELOPMENT_ACCESS_SECRETS_REQUIRED');
async function equal(a: string, b: string) {
  const hash = (s: string) => crypto.subtle.digest('SHA-256', new TextEncoder().encode(s));
  const [x, y] = await Promise.all([hash(a), hash(b)]);
  const u = new Uint8Array(x),
    v = new Uint8Array(y);
  let diff = 0;
  for (let i = 0; i < u.length; i++) diff |= u[i] ^ v[i];
  return diff === 0;
}
export const handle: Handle = async ({ event, resolve }) => {
  if (protectedMode) {
    const expected = 'Basic ' + btoa(DEVELOPMENT_USER + ':' + DEVELOPMENT_PASSWORD);
    if (!(await equal(event.request.headers.get('authorization') || '', expected)))
      return new Response('Authentication required', {
        status: 401,
        headers: {
          'WWW-Authenticate': 'Basic realm="Sarjy development", charset="UTF-8"',
          'Cache-Control': 'no-store',
        },
      });
  }
  return resolve(event);
};
