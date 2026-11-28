import type { Cookies } from '@sveltejs/kit';
import { API_INTERNAL_URL, WEB_SERVICE_SECRET } from '$app/env/private';
import { ACCOUNT_COOKIE, issuedSession, type AccountAction } from './account';

/** The API owns accounts and sessions; this is the web edge's only conversation with it. */
const VERIFY_TIMEOUT_MS = 5_000;
// Longer than the API's own deadline for the credential provider.
const SUBMIT_TIMEOUT_MS = 12_000;

type CallOptions = { token?: string; method?: string; headers?: object; body?: string; ms: number };

function call(path: string, options: CallOptions) {
  return fetch(new URL(path, API_INTERNAL_URL), {
    method: options.method ?? 'GET',
    headers: {
      'x-web-service-secret': WEB_SERVICE_SECRET,
      ...(options.token ? { cookie: `${ACCOUNT_COOKIE}=${options.token}` } : {}),
      ...options.headers,
    },
    body: options.body,
    redirect: 'manual',
    signal: AbortSignal.timeout(options.ms),
  });
}

const RETRYABLE_STATUSES = new Set([502, 503, 504]);

function isTimeout(error: unknown): boolean {
  if (typeof error !== 'object' || error === null || !('name' in error)) return false;
  return error.name === 'TimeoutError' || error.name === 'AbortError';
}

/**
 * GET is idempotent, so one immediate retry is safe: a kept-alive connection to
 * the API's ingress can be stale, and an ingress 502/503/504 during a deploy is
 * transient. A timeout is not retried: the visitor already waited the deadline.
 */
async function getWithRetry(path: string, options: CallOptions): Promise<Response> {
  try {
    const response = await call(path, options);
    if (!RETRYABLE_STATUSES.has(response.status)) return response;
    await response.body?.cancel();
  } catch (error) {
    if (isTimeout(error)) throw error;
  }
  return call(path, { ...options, headers: { ...options.headers, connection: 'close' } });
}

/** Cookie values are only forwarded in the exact shape the API issues. */
function sessionToken(cookies: Cookies): string | undefined {
  const value = cookies.get(ACCOUNT_COOKIE);
  return value && /^[A-Za-z0-9_-]{43}$/.test(value) ? value : undefined;
}

export async function accountSession(cookies: Cookies): Promise<'valid' | 'none' | 'unavailable'> {
  const token = sessionToken(cookies);
  if (!token) return 'none';
  try {
    const response = await getWithRetry('/v1/auth/session', { token, ms: VERIFY_TIMEOUT_MS });
    await response.body?.cancel();
    if (response.ok) return 'valid';
    return response.status === 401 ? 'none' : 'unavailable';
  } catch {
    return 'unavailable';
  }
}

/** POST a JSON body to the API and adopt the session it issues; returns the failure code, or `null`. */
async function exchange(
  path: string,
  body: string,
  context: { cookies: Cookies; source: string },
): Promise<string | null> {
  try {
    const response = await call(path, {
      token: sessionToken(context.cookies),
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-reviewer-source': context.source },
      body,
      ms: SUBMIT_TIMEOUT_MS,
    });
    if (!response.ok) {
      const failure = (await response.json()) as { detail?: { code?: unknown } } | null;
      const code = failure?.detail?.code;
      return typeof code === 'string' ? code : 'ACCOUNT_SERVICE_UNAVAILABLE';
    }
    await response.body?.cancel();
    const session = issuedSession(response.headers.getSetCookie());
    if (!session) return 'ACCOUNT_SERVICE_UNAVAILABLE';
    context.cookies.set(ACCOUNT_COOKIE, session.token, {
      path: '/',
      httpOnly: true,
      secure: true,
      sameSite: 'lax',
      maxAge: session.maxAge,
    });
    return null;
  } catch {
    return 'ACCOUNT_SERVICE_UNAVAILABLE';
  }
}

/** Exchange credentials for a session cookie; returns the failure code, or `null` on success. */
export function enterAccount(
  action: AccountAction,
  credentials: { email: string; password: string },
  context: { cookies: Cookies; source: string },
): Promise<string | null> {
  return exchange(`/v1/auth/${action}`, JSON.stringify(credentials), context);
}

/** Exchange a Supabase OAuth code for a session cookie; returns the failure code, or `null`. */
export function exchangeOAuthCode(
  code: string,
  verifier: string,
  context: { cookies: Cookies; source: string },
): Promise<string | null> {
  return exchange('/v1/auth/oauth/exchange', JSON.stringify({ code, code_verifier: verifier }), context);
}

/** Revoke the session at the API, then forget it here even when the API could not be reached. */
export async function leaveAccount(cookies: Cookies): Promise<void> {
  const token = sessionToken(cookies);
  if (token) {
    try {
      const response = await call('/v1/auth/sign-out', { token, method: 'POST', ms: VERIFY_TIMEOUT_MS });
      await response.body?.cancel();
      if (!response.ok) console.error(`account access: sign-out answered HTTP ${response.status}`);
    } catch {
      console.error('account access: sign-out could not reach the API; the session ends at its expiry');
    }
  }
  cookies.delete(ACCOUNT_COOKIE, { path: '/', httpOnly: true, secure: true, sameSite: 'lax' });
}
