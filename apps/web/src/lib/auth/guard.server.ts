import type { Cookies } from '@sveltejs/kit';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import {
  ACCOUNT_ACCESS_ENABLED,
  DEVELOPMENT_PROTECTED,
  OAUTH_PROVIDERS,
  REVIEW_ACCESS_EXPIRES_AT,
  REVIEW_ACCESS_MAX_USES,
  REVIEW_ACCESS_STATE_PATH,
  REVIEW_ACCESS_TOKEN,
  REVIEWER_ACCESS_ENABLED,
  SESSION_SECRET,
  SUPABASE_URL,
  WEB_SERVICE_SECRET,
} from '$app/env/private';
import { createAccessStateStore } from './access-state';
import { redeemAccessToken, type AccessResult } from './access';
import { isLocalOrigin, parseAccountMode, parseProtectedMode } from './config';
import { resolveAccessExpiry } from './expiry';
import { parseOAuthProviders } from './oauth';
import { createFailureTrackers } from './rate-limit';
import { isReviewerMode, REVIEWER_COOKIE } from './reviewer';
import { SESSION_COOKIE, signSession, tokenFingerprint, verifySession } from './session';

/**
 * Server-only wiring between the environment and the pure access policy.
 * Nothing here is importable from the client bundle (`$app/env/private` is a
 * server-only module and this file is named `.server.ts`).
 */
const protectedSetting = DEVELOPMENT_PROTECTED;
export const protectedMode = parseProtectedMode(protectedSetting);
// A deploy that forgets the flag but configures a real ORIGIN must not serve
// the app unprotected. Local development (no ORIGIN, or localhost) is exempt.
if (protectedSetting === undefined && !isLocalOrigin(process.env.ORIGIN)) {
  throw new Error('DEVELOPMENT_PROTECTED must be set explicitly when ORIGIN is not localhost');
}
export const sessionSecret = SESSION_SECRET || WEB_SERVICE_SECRET;
export const accessToken = REVIEW_ACCESS_TOKEN;
export const maxUses = Number.parseInt(REVIEW_ACCESS_MAX_USES, 10);
// Reviewer mode is additive: an invitation session from the API authorises the
// visitor, while the shared token door keeps its #142 configuration, deadline
// and failure accounting. Enabling invitations never weakens the base gate.
export const reviewerMode = isReviewerMode(REVIEWER_ACCESS_ENABLED);
// Accounts replace the shared token and the invitations; neither is configured or consulted then.
export const accountMode = parseAccountMode(ACCOUNT_ACCESS_ENABLED);
// Refuse to boot behind the token door when the absolute deadline is missing or
// unparseable. An unprotected deployment has no deadline to enforce.
export const accessExpiresAt =
  protectedMode && !accountMode ? resolveAccessExpiry(REVIEW_ACCESS_EXPIRES_AT) : Number.NaN;
// `/tmp` is writable in the adapter-node container; a relative cwd is not
// guaranteed to be. Operators can override with REVIEW_ACCESS_STATE_PATH.
export const accessState = createAccessStateStore({
  path: resolve(REVIEW_ACCESS_STATE_PATH || join(tmpdir(), 'sarjy-review-access-state.json')),
});
export const accessFailures = createFailureTrackers({ addressLimit: 100, tokenLimit: 10, windowMs: 15 * 60_000 });

const fingerprint = sessionSecret ? tokenFingerprint(accessToken, sessionSecret) : '';

function isSafeSupabaseUrl(value: string): boolean {
  try {
    const url = new URL(value);
    const local = url.protocol === 'http:' && (url.hostname === 'localhost' || url.hostname === '127.0.0.1');
    return url.protocol === 'https:' || local;
  } catch {
    return false;
  }
}

export function assertAccessConfigured(): void {
  if (accountMode) {
    if (!protectedMode || reviewerMode || !WEB_SERVICE_SECRET) throw new Error('ACCOUNT_ACCESS_CONFIGURATION_INVALID');
    // Without it every visitor is the proxy, and anyone could use up the attempts of all.
    if (!process.env.ADDRESS_HEADER) throw new Error('ACCOUNT_ACCESS_REQUIRES_ADDRESS_HEADER');
    const providers = parseOAuthProviders(OAUTH_PROVIDERS);
    if (providers.length > 0 && !isSafeSupabaseUrl(SUPABASE_URL)) {
      throw new Error('ACCOUNT_OAUTH_REQUIRES_HTTPS');
    }
    return;
  }
  if (!protectedMode) return;
  if (
    !accessToken ||
    !sessionSecret ||
    !Number.isFinite(accessExpiresAt) ||
    !Number.isInteger(maxUses) ||
    maxUses <= 0
  ) {
    throw new Error('REVIEW_ACCESS_CONFIGURATION_REQUIRED');
  }
  if (!SESSION_SECRET) {
    console.warn(
      'review access: SESSION_SECRET unset; signing with WEB_SERVICE_SECRET (the API secret can mint reviewer cookies)',
    );
  }
  if (!process.env.ADDRESS_HEADER) {
    console.warn(
      'review access: ADDRESS_HEADER unset; the failure cap keys on the socket address and every reviewer behind a proxy may share one bucket',
    );
  }
}

export function sessionIsValid(cookies: Cookies): boolean {
  if (!protectedMode || accountMode) return false;
  return verifySession(cookies.get(SESSION_COOKIE), sessionSecret, {
    fingerprint,
    maxExpiresAt: accessExpiresAt,
  });
}

/**
 * The web edge cannot verify a revocable reviewer session without calling the
 * API on every request. Presence is enough to stop the shared token door from
 * hiding the app shell; the API rejects an invalid, expired or revoked cookie
 * on the first `/v1/*` call.
 */
export function reviewerSessionPresent(cookies: Cookies): boolean {
  return reviewerMode && Boolean(cookies.get(REVIEWER_COOKIE));
}

export function redeem(candidate: string): Promise<AccessResult> {
  return redeemAccessToken({
    candidate,
    config: { token: accessToken, expiresAt: accessExpiresAt, maxUses },
    store: accessState,
  });
}

/** Set the signed cookie for a successful exchange. Never logs the value. */
export function issueSession(cookies: Cookies, granted: { expiresAt: number }): boolean {
  const signed = signSession(sessionSecret, { fingerprint, expiresAt: granted.expiresAt });
  if (!signed) return false;
  cookies.set(SESSION_COOKIE, signed.token, {
    path: '/',
    httpOnly: true,
    secure: true,
    sameSite: 'lax',
    maxAge: signed.maxAge,
  });
  return true;
}

export function clearSession(cookies: Cookies): void {
  cookies.delete(SESSION_COOKIE, { path: '/', httpOnly: true, secure: true, sameSite: 'lax' });
}

/**
 * Clear the API-issued reviewer identity so sign-out does not leave a reviewer
 * cookie behind. The API owns the cookie's authority; deleting it here only
 * expires the copy in this browser.
 */
export function clearReviewerIdentity(cookies: Cookies): void {
  if (!reviewerMode) return;
  cookies.delete(REVIEWER_COOKIE, { path: '/' });
}
