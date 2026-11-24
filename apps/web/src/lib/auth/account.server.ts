import type { Cookies } from '@sveltejs/kit';
import { API_INTERNAL_URL, WEB_SERVICE_SECRET } from '$app/env/private';
import { ACCOUNT_COOKIE, issuedSession, type AccountAction } from './account';

/** The API owns accounts and sessions; this is the web edge's only conversation with it. */
const VERIFY_TIMEOUT_MS = 5_000;
// Longer than the API's own deadline for the credential provider.
const SUBMIT_TIMEOUT_MS = 12_000;

function call(path: string, options: { token?: string; method?: string; headers?: object; body?: string; ms: number }) {
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

/** Cookie values are only forwarded in the exact shape the API issues. */
function sessionToken(cookies: Cookies): string | undefined {
  const value = cookies.get(ACCOUNT_COOKIE);
  return value && /^[A-Za-z0-9_-]{43}$/.test(value) ? value : undefined;
}

export async function accountSession(cookies: Cookies): Promise<'valid' | 'none' | 'unavailable'> {
  const token = sessionToken(cookies);
  if (!token) return 'none';
  try {
    const response = await call('/v1/auth/session', { token, ms: VERIFY_TIMEOUT_MS });
    await response.body?.cancel();
    if (response.ok) return 'valid';
    return response.status === 401 ? 'none' : 'unavailable';
  } catch {
    return 'unavailable';
  }
}

/** Exchange credentials for a session cookie; returns the failure code, or `null` on success. */
export async function enterAccount(
  action: AccountAction,
  credentials: { email: string; password: string },
  context: { cookies: Cookies; source: string },
): Promise<string | null> {
  try {
    const response = await call(`/v1/auth/${action}`, {
      token: sessionToken(context.cookies),
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-reviewer-source': context.source },
      body: JSON.stringify(credentials),
      ms: SUBMIT_TIMEOUT_MS,
    });
    if (!response.ok) {
      const body = (await response.json()) as { detail?: { code?: unknown } } | null;
      const code = body?.detail?.code;
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
