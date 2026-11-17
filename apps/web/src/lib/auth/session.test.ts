import { describe, expect, it } from 'vitest';
import { SESSION_COOKIE, secureEqual, signSession, tokenFingerprint, verifySession } from './session';

const SECRET = 'session-secret-for-tests-only';
const FINGERPRINT = tokenFingerprint('access-token-value', SECRET);
const HOUR = 3_600_000;

function sign(now: number, expiresAt: number) {
  return signSession(SECRET, { fingerprint: FINGERPRINT, expiresAt, now });
}

describe('review session cookie', () => {
  it('round-trips a signed session and uses the documented four fields', () => {
    const now = 1_700_000_000_000;
    const signed = sign(now, now + 12 * HOUR);
    expect(signed).not.toBeNull();
    expect(signed!.token.split('.')).toHaveLength(4);
    expect(verifySession(signed!.token, SECRET, { fingerprint: FINGERPRINT, maxExpiresAt: now + 12 * HOUR, now })).toBe(
      true,
    );
    expect(signed!.token).not.toContain('access-token-value');
    expect(SESSION_COOKIE).toBe('sarjy_review_session');
  });

  it('rejects a tampered signature', () => {
    const now = 1_700_000_000_000;
    const signed = sign(now, now + HOUR)!;
    const parts = signed.token.split('.');
    // Mutate a decoded byte. Rewriting the final base64url character is not a
    // reliable tamper: it carries padding bits, so some replacements decode to
    // the same signature and the MAC still verifies (about 1 run in 16).
    const signature = Buffer.from(parts[3]!, 'base64url');
    signature[0] = (signature[0] ?? 0) ^ 0x01;
    parts[3] = signature.toString('base64url');
    expect(verifySession(parts.join('.'), SECRET, { fingerprint: FINGERPRINT, maxExpiresAt: now + HOUR, now })).toBe(
      false,
    );
  });

  it('rejects an expired cookie', () => {
    const now = 1_700_000_000_000;
    const signed = sign(now, now + HOUR)!;
    expect(
      verifySession(signed.token, SECRET, { fingerprint: FINGERPRINT, maxExpiresAt: now + HOUR, now: now + HOUR }),
    ).toBe(false);
  });

  it('rejects a cookie whose issued_at is in the future', () => {
    const now = 1_700_000_000_000;
    const signed = signSession(SECRET, { fingerprint: FINGERPRINT, expiresAt: now + 2 * HOUR, now: now + HOUR })!;
    expect(verifySession(signed.token, SECRET, { fingerprint: FINGERPRINT, maxExpiresAt: now + 2 * HOUR, now })).toBe(
      false,
    );
  });

  it('rejects a cookie issued for a rotated access token', () => {
    const now = 1_700_000_000_000;
    const signed = sign(now, now + HOUR)!;
    const rotated = tokenFingerprint('rotated-token', SECRET);
    expect(verifySession(signed.token, SECRET, { fingerprint: rotated, maxExpiresAt: now + HOUR, now })).toBe(false);
  });

  it('never accepts a cookie that outlives the absolute token expiry', () => {
    const now = 1_700_000_000_000;
    const signed = sign(now, now + HOUR)!;
    expect(verifySession(signed.token, SECRET, { fingerprint: FINGERPRINT, maxExpiresAt: now + HOUR / 2, now })).toBe(
      false,
    );
  });

  it('compares secrets without early exit on length', () => {
    expect(secureEqual('same-value', 'same-value')).toBe(true);
    expect(secureEqual('same-value', 'same-valuf')).toBe(false);
    expect(secureEqual('short', 'a much longer value')).toBe(false);
  });
});
