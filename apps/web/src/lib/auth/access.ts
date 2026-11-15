import { tokenKey, type AccessStateStore } from './access-state';
import { secureEqual } from './session';
/**
 * Pure access-token policy. The guard wires configuration into this, tests
 * exercise it directly, and every failed branch maps to the same generic copy
 * at the HTTP layer so callers cannot distinguish invalid from expired from
 * exhausted.
 */
export interface AccessConfig {
  token: string;
  /** Absolute expiry in milliseconds. */
  expiresAt: number;
  maxUses: number;
}

export type AccessFailure = 'invalid' | 'expired' | 'exhausted' | 'unavailable';

export type AccessResult = { ok: true; expiresAt: number; remaining: number } | { ok: false; reason: AccessFailure };

export interface RedeemAccessOptions {
  candidate: string;
  config: AccessConfig;
  store: Pick<AccessStateStore, 'redeem'>;
  now?: number;
}

export async function redeemAccessToken({
  candidate,
  config,
  store,
  now = Date.now(),
}: RedeemAccessOptions): Promise<AccessResult> {
  if (!candidate || !config.token || !secureEqual(candidate, config.token)) return { ok: false, reason: 'invalid' };
  // Check wall-clock expiry before touching the counter: an expired token must
  // never consume a use.
  if (!Number.isFinite(config.expiresAt) || now >= config.expiresAt) return { ok: false, reason: 'expired' };
  if (!Number.isInteger(config.maxUses) || config.maxUses <= 0) return { ok: false, reason: 'exhausted' };
  try {
    const outcome = await store.redeem(tokenKey(candidate), config.maxUses);
    if (!outcome.ok) return { ok: false, reason: 'exhausted' };
    return { ok: true, expiresAt: config.expiresAt, remaining: outcome.remaining };
  } catch {
    return { ok: false, reason: 'unavailable' };
  }
}
