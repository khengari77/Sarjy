import { describe, expect, it } from 'vitest';
import { displayedErrorCode } from './errorCodes';

describe('displayedErrorCode', () => {
  it.each([
    'DAILY_BUDGET_EXHAUSTED',
    'BUDGET_SERVICE_UNAVAILABLE',
    'CONTEXT_BUDGET_EXCEEDED',
    'PROVIDER_REQUEST_TOO_LARGE',
  ])('accepts %s', (code) => {
    expect(displayedErrorCode(code)).toBe(code);
  });

  it.each([undefined, null, 42, {}, '', 'UNKNOWN', '<script>'])('drops %s', (value) => {
    expect(displayedErrorCode(value)).toBeNull();
  });
});
