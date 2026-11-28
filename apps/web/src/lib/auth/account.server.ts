import type { Cookies } from '@sveltejs/kit';
import { API_INTERNAL_URL, WEB_SERVICE_SECRET } from '$app/env/private';
import { ACCOUNT_COOKIE, issuedSession, type AccountAction } from './account';

/** The API owns accounts and sessions; this is the web edge's only conversation with it. */
const VERIFY_TIMEOUT_MS = 5_000;
// Longer than the API's own deadline for the credential provider.
const SUBMIT_TIMEOUT_MS = 12_000;

type CallOptions = { token?: string; method?: string; headers?: object; body?: string; ms: number };

const UNAVAILABLE_LOG_INTERVAL_MS = 10_000;
let lastUnavailableLogAt: number | undefined;
let suppressedUnavailableLogs = 0;

type UnavailableDetails = {
  reason: 'timeout' | 'network' | 'status';
  status?: number;
  name?: string;
  code?: string;
  attempts: number;
  ms: number;
};

function errorName(error: unknown): string | undefined {
  if (typeof error !== 'object' || error === null || !('name' in error)) return undefined;
  return typeof error.name === 'string' ? error.name : undefined;
}

function errorCode(error: unknown): string | undefined {
  if (typeof error !== 'object' || error === null) return undefined;
  const cause = 'cause' in error ? error.cause : undefined;
  const candidate =
    typeof cause === 'object' && cause !== null && 'code' in cause
      ? cause.code
      : 'code' in error
        ? error.code
        : undefined;
  return typeof candidate === 'string' ? candidate : undefined;
}

/**
 * One structured warning per outage, rate-limited so a failing API cannot flood
 * the web log. Never include the cookie, token, secret, URL or headers.
 */
function warnUnavailable(details: UnavailableDetails): void {
  const now = Date.now();
  if (lastUnavailableLogAt !== undefined && now - lastUnavailableLogAt < UNAVAILABLE_LOG_INTERVAL_MS) {
    suppressedUnavailableLogs += 1;
    return;
  }
  lastUnavailableLogAt = now;
  const suppressed = suppressedUnavailableLogs;
  suppressedUnavailableLogs = 0;
  console.warn('[Sarjy] account session check unavailable', suppressed > 0 ? { ...details, suppressed } : details);
}

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
 * GET is idempotent, so one immediate retry is safe: a stale kept-alive
 * connection to the API's ingress fails before reaching it, and undici drops
 * that failed socket, so the retry opens or reuses a healthy one. An ingress
 * 502/503/504 during a deploy is equally transient. A timeout is not retried:
 * the visitor already waited the deadline.
 */
async function getWithRetry(path: string, options: CallOptions, progress: { attempts: number }): Promise<Response> {
  try {
    progress.attempts += 1;
    const response = await call(path, options);
    if (!RETRYABLE_STATUSES.has(response.status)) return response;
    try {
      await response.body?.cancel();
    } catch {
      // The retry must proceed even when the discarded body cannot be cancelled.
    }
  } catch (error) {
    if (isTimeout(error)) throw error;
  }
  progress.attempts += 1;
  return call(path, options);
}

/** Cookie values are only forwarded in the exact shape the API issues. */
function sessionToken(cookies: Cookies): string | undefined {
  const value = cookies.get(ACCOUNT_COOKIE);
  return value && /^[A-Za-z0-9_-]{43}$/.test(value) ? value : undefined;
}

export async function accountSession(cookies: Cookies): Promise<'valid' | 'none' | 'unavailable'> {
  const token = sessionToken(cookies);
  if (!token) return 'none';
  const startedAt = Date.now();
  const progress = { attempts: 0 };
  try {
    const response = await getWithRetry('/v1/auth/session', { token, ms: VERIFY_TIMEOUT_MS }, progress);
    await response.body?.cancel();
    if (response.ok) return 'valid';
    if (response.status === 401) return 'none';
    warnUnavailable({
      reason: 'status',
      status: response.status,
      attempts: progress.attempts,
      ms: Date.now() - startedAt,
    });
    return 'unavailable';
  } catch (error) {
    const details: UnavailableDetails = {
      reason: isTimeout(error) ? 'timeout' : 'network',
      attempts: progress.attempts,
      ms: Date.now() - startedAt,
    };
    const name = errorName(error);
    const code = errorCode(error);
    if (name) details.name = name;
    if (code) details.code = code;
    warnUnavailable(details);
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
