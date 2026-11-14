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
