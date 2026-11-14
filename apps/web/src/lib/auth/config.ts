/**
 * Strict parsing for the gate switch. A typo must fail boot rather than serve
 * the full app without a token.
 */
export const PROTECTED_MODE_ERROR = 'DEVELOPMENT_PROTECTED must be exactly "true" or "false"';

export function parseProtectedMode(value: string | undefined | null): boolean {
  if (value === 'true') return true;
  if (value === 'false' || value === undefined || value === null || value === '') return false;
  throw new Error(PROTECTED_MODE_ERROR);
}
