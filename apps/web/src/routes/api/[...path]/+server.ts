import { API_INTERNAL_URL, WEB_SERVICE_SECRET } from '$app/env/private';
import type { RequestHandler } from '@sveltejs/kit';
import { proxyTimeoutMs } from '../../../lib/proxy-deadline';

const proxy: RequestHandler = async ({ request, params, url }) => {
  if (!['GET', 'POST'].includes(request.method)) return new Response(null, { status: 405 });
  if (request.method === 'POST' && request.headers.get('origin') !== url.origin) {
    return Response.json({ detail: { code: 'INVALID_ORIGIN' } }, { status: 403 });
  }
  const path = params.path ?? '';
  if (!path.startsWith('v1/') || path.includes('..')) return new Response(null, { status: 404 });
  const target = new URL('/' + path, API_INTERNAL_URL);
  target.search = url.search;
  const headers = new Headers();
  if (WEB_SERVICE_SECRET) headers.set('x-web-service-secret', WEB_SERVICE_SECRET);
  for (const name of ['content-type', 'cookie', 'idempotency-key']) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  try {
    const result = await fetch(target, {
      method: request.method,
      headers,
      body: request.method === 'POST' ? await request.arrayBuffer() : undefined,
      redirect: 'manual',
      signal: AbortSignal.timeout(proxyTimeoutMs(path, request.method)),
    });
    const outgoing = new Headers({ 'cache-control': 'no-store' });
    outgoing.set('content-type', result.headers.get('content-type') || 'application/json');
    for (const cookie of result.headers.getSetCookie()) outgoing.append('set-cookie', cookie);
    return new Response(result.body, { status: result.status, headers: outgoing });
  } catch {
    return Response.json({ detail: { code: 'API_UNAVAILABLE' } }, { status: 503 });
  }
};
export const GET = proxy;
export const POST = proxy;
