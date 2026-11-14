import type { Cookies } from '@sveltejs/kit';
import { resolve } from 'node:path';
import {
  DEVELOPMENT_PROTECTED,
  REVIEW_ACCESS_DAYS,
  REVIEW_ACCESS_EXPIRES_AT,
  REVIEW_ACCESS_MAX_USES,
  REVIEW_ACCESS_STATE_PATH,
  REVIEW_ACCESS_TOKEN,
  SESSION_SECRET,
  WEB_SERVICE_SECRET,
} from '$app/env/private';
import { createAccessStateStore } from './access-state';
import { redeemAccessToken, type AccessResult } from './access';
import { createRateLimiter } from './rate-limit';
import { SESSION_COOKIE, signSession, tokenFingerprint, verifySession } from './session';

/**
 * Server-only wiring between the environment and the pure access policy.
 * Nothing here is importable from the client bundle (`$app/env/private` is a
 * server-only module and this file is named `.server.ts`).
 */
export const protectedMode = DEVELOPMENT_PROTECTED === 'true';
export const sessionSecret = SESSION_SECRET || WEB_SERVICE_SECRET;
export const accessToken = REVIEW_ACCESS_TOKEN;
export const maxUses = Number.parseInt(REVIEW_ACCESS_MAX_USES, 10);
export const accessExpiresAt = resolveExpiry();
export const accessState = createAccessStateStore({
  path: resolve(REVIEW_ACCESS_STATE_PATH || '.sarjy/review-access-state.json'),
});
export const accessFailures = createRateLimiter({ limit: 10, windowMs: 15 * 60_000 });

const fingerprint = sessionSecret ? tokenFingerprint(accessToken, sessionSecret) : '';

/**
 * `REVIEW_ACCESS_EXPIRES_AT` is authoritative. `REVIEW_ACCESS_DAYS` is an
 * optional convenience that resolves to a fixed instant once, at boot; it does
 * not slide forward on later requests or restarts beyond the boot computation.
 */
function resolveExpiry(): number {
  const absolute = Date.parse(REVIEW_ACCESS_EXPIRES_AT);
  if (Number.isFinite(absolute)) return absolute;
  const days = Number.parseFloat(REVIEW_ACCESS_DAYS);
  if (Number.isFinite(days) && days > 0) return Date.now() + days * 86_400_000;
  return Number.NaN;
}

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
