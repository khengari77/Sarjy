/**
 * The reviewer access deadline is an absolute wall-clock instant supplied by
 * configuration. It is resolved deterministically from
 * `REVIEW_ACCESS_EXPIRES_AT`; there is deliberately no "days from now"
 * fallback, because that would restart the window on every boot and extend
 * links beyond the operator's intent.
 */
export const ACCESS_EXPIRY_ERROR =
  'REVIEW_ACCESS_EXPIRES_AT must be an ISO-8601 UTC instant (for example 2026-12-31T23:59:59Z)';

export function resolveAccessExpiry(value: string | undefined | null): number {
  if (!value) throw new Error(ACCESS_EXPIRY_ERROR);
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) throw new Error(ACCESS_EXPIRY_ERROR);
  return parsed;
}
