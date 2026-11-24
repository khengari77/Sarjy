/**
 * Strict parsing for the gate switch. A typo must fail boot rather than serve
 * the full app without a token.
 */
export const PROTECTED_MODE_ERROR = 'DEVELOPMENT_PROTECTED must be exactly "true" or "false"';

export function parseProtectedMode(value: string | undefined | null): boolean {
  if (value === 'true') return true;
  if (value === 'false' || value === undefined || value === null) return false;
  throw new Error(PROTECTED_MODE_ERROR);
}

/** Same strictness for the account switch: a typo must not silently serve the other door. */
export function parseAccountMode(value: string | undefined | null): boolean {
  if (value === 'true') return true;
  if (value === 'false' || value === undefined || value === null) return false;
  throw new Error('ACCOUNT_ACCESS_ENABLED must be exactly "true" or "false"');
}

/** Whether a configured ORIGIN points at a developer's own machine. */
export function isLocalOrigin(origin: string | undefined): boolean {
  if (!origin) return true;
  try {
    const host = new URL(origin).hostname;
    return host === 'localhost' || host === '127.0.0.1' || host === '::1';
  } catch {
    return false;
  }
}
