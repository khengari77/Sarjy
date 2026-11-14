import { createHash, createHmac, randomBytes, timingSafeEqual } from 'node:crypto';

/**
 * Signed reviewer session cookie.
 *
 * Wire format: `<nonce>.<issued_at>.<expires_at>.<hmac>` where the times are
 * integer seconds since the epoch. The HMAC-SHA256 input is the first three
 * fields plus a keyed fingerprint of the shared access token, so rotating
 * `REVIEW_ACCESS_TOKEN` invalidates every cookie issued for the old value.
 *
 * The access token itself, or an unkeyed hash of it, never appears in the
 * cookie or in any log.
 */
export const SESSION_COOKIE = 'sarjy_review_session';

const MAC_CONTEXT = 'review-session:v1';
const VERSION_FIELD = /^[A-Za-z0-9_-]{1,128}$/;
const BASE64URL_SHA256 = /^[A-Za-z0-9_-]{43}$/;
const EPOCH_SECONDS = /^\d{1,12}$/;
const NONCE_BYTES = 18;
const CLOCK_SKEW_MS = 60_000;

function hmac(secret: string, payload: string): Buffer {
  return createHmac('sha256', secret).update(payload, 'utf8').digest();
}

function encode(value: Buffer): string {
  return value.toString('base64url');
}

function macInput(unsigned: string, fingerprint: string): string {
  return `${MAC_CONTEXT}:${unsigned}.${fingerprint}`;
}

/** Length-independent comparison of two strings. */
export function secureEqual(left: string, right: string): boolean {
  const a = createHash('sha256').update(left, 'utf8').digest();
  const b = createHash('sha256').update(right, 'utf8').digest();
  return timingSafeEqual(a, b);
}

/**
 * Keyed fingerprint of the access token. Used inside the cookie HMAC so a
 * token rotation rejects outstanding sessions without ever storing the token.
 */
export function tokenFingerprint(token: string, secret: string): string {
  return encode(hmac(secret, `review-token:${token}`));
}

export interface SignedSession {
  token: string;
  /** Absolute expiry in milliseconds (the token's authoritative instant). */
  expiresAt: number;
  /** Remaining lifetime in seconds, for `Set-Cookie: Max-Age`. */
  maxAge: number;
}

export interface SignSessionOptions {
  fingerprint: string;
  /** Absolute expiry in milliseconds. */
  expiresAt: number;
  now?: number;
  nonce?: string;
}

/**
 * Create a session cookie value. Returns null when the token is already
 * expired or configuration is missing; callers must not set a cookie then.
 */
export function signSession(secret: string, options: SignSessionOptions): SignedSession | null {
  const now = options.now ?? Date.now();
  const expiresAtSec = Math.floor(options.expiresAt / 1000);
  const nowSec = Math.floor(now / 1000);
  const maxAge = expiresAtSec - nowSec;
  if (!secret || !options.fingerprint || !Number.isFinite(options.expiresAt) || maxAge <= 0) return null;

  const nonce = options.nonce ?? encode(randomBytes(NONCE_BYTES));
  const unsigned = `${nonce}.${nowSec}.${expiresAtSec}`;
  const signature = encode(hmac(secret, macInput(unsigned, options.fingerprint)));
  return { token: `${unsigned}.${signature}`, expiresAt: expiresAtSec * 1000, maxAge };
}

export interface VerifySessionOptions {
  fingerprint: string;
  /** Authoritative `REVIEW_ACCESS_EXPIRES_AT` in milliseconds. */
  maxExpiresAt: number;
  now?: number;
}

/**
 * Verify signature, age and the token fingerprint. Any mismatch is treated as
 * "no session": tampered, expired, issued in the future, issued for a rotated
 * token, or carrying an expiry beyond the configured absolute instant.
 */
export function verifySession(
  token: string | null | undefined,
  secret: string,
  options: VerifySessionOptions,
): boolean {
  if (!token || !secret || !options.fingerprint || !Number.isFinite(options.maxExpiresAt)) return false;
  const parts = token.split('.');
  if (parts.length !== 4) return false;
  const [nonce, issuedText, expiresText, signature] = parts;
  if (!VERSION_FIELD.test(nonce)) return false;
  if (!EPOCH_SECONDS.test(issuedText) || !EPOCH_SECONDS.test(expiresText)) return false;
  if (!BASE64URL_SHA256.test(signature)) return false;

  const now = options.now ?? Date.now();
  const issuedAt = Number(issuedText) * 1000;
  const expiresAt = Number(expiresText) * 1000;
  if (issuedAt > now + CLOCK_SKEW_MS) return false;
  if (issuedAt >= expiresAt) return false;
  if (expiresAt <= now) return false;
  if (expiresAt > options.maxExpiresAt) return false;

  const expected = hmac(secret, macInput(`${nonce}.${issuedText}.${expiresText}`, options.fingerprint));
  const provided = Buffer.from(signature, 'base64url');
  if (provided.length !== expected.length) return false;
  if (!timingSafeEqual(provided, expected)) return false;
  return true;
}
