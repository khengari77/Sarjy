/**
 * Error codes the worker is allowed to surface in the room.
 *
 * Unknown or malformed codes are dropped instead of being rendered verbatim,
 * so a compromised or buggy provider frame cannot inject arbitrary UI text.
 */
const DISPLAYED_ERROR_CODES = new Set([
  'DAILY_BUDGET_EXHAUSTED',
  'BUDGET_SERVICE_UNAVAILABLE',
  'CONTEXT_BUDGET_EXCEEDED',
  'PROVIDER_REQUEST_TOO_LARGE',
]);

export function displayedErrorCode(value: unknown): string | null {
  return typeof value === 'string' && DISPLAYED_ERROR_CODES.has(value) ? value : null;
}
