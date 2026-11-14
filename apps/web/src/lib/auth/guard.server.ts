import type { Cookies } from '@sveltejs/kit';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import {
  DEVELOPMENT_PROTECTED,
  REVIEW_ACCESS_EXPIRES_AT,
  REVIEW_ACCESS_MAX_USES,
  REVIEW_ACCESS_STATE_PATH,
  REVIEW_ACCESS_TOKEN,
  SESSION_SECRET,
  WEB_SERVICE_SECRET,
} from '$app/env/private';
import { createAccessStateStore } from './access-state';
import { redeemAccessToken, type AccessResult } from './access';
import { parseProtectedMode } from './config';
import { resolveAccessExpiry } from './expiry';
import { createRateLimiter } from './rate-limit';
import { SESSION_COOKIE, signSession, tokenFingerprint, verifySession } from './session';

/**
 * Server-only wiring between the environment and the pure access policy.
 * Nothing here is importable from the client bundle (`$app/env/private` is a
 * server-only module and this file is named `.server.ts`).
 */
export const protectedMode = parseProtectedMode(DEVELOPMENT_PROTECTED);
export const sessionSecret = SESSION_SECRET || WEB_SERVICE_SECRET;
export const accessToken = REVIEW_ACCESS_TOKEN;
export const maxUses = Number.parseInt(REVIEW_ACCESS_MAX_USES, 10);
// Refuse to boot in protected mode when the absolute deadline is missing or
// unparseable. An unprotected deployment has no deadline to enforce.
export const accessExpiresAt = protectedMode ? resolveAccessExpiry(REVIEW_ACCESS_EXPIRES_AT) : Number.NaN;
// `/tmp` is writable in the adapter-node container; a relative cwd is not
// guaranteed to be. Operators can override with REVIEW_ACCESS_STATE_PATH.
export const accessState = createAccessStateStore({
  path: resolve(REVIEW_ACCESS_STATE_PATH || join(tmpdir(), 'sarjy-review-access-state.json')),
});
export const accessFailures = createRateLimiter({ limit: 10, windowMs: 15 * 60_000 });

const fingerprint = sessionSecret ? tokenFingerprint(accessToken, sessionSecret) : '';

export function assertAccessConfigured(): void {
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
  if (!protectedMode) return false;
  return verifySession(cookies.get(SESSION_COOKIE), sessionSecret, {
    fingerprint,
    maxExpiresAt: accessExpiresAt,
  });
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
