import { describe, expect, it } from 'vitest';
import { PROTECTED_MODE_ERROR, parseProtectedMode } from './config';

describe('DEVELOPMENT_PROTECTED', () => {
  it('accepts the two documented values', () => {
    expect(parseProtectedMode('true')).toBe(true);
    expect(parseProtectedMode('false')).toBe(false);
  });

  it('treats unset/empty as the documented unprotected default', () => {
    expect(parseProtectedMode(undefined)).toBe(false);
    expect(parseProtectedMode('')).toBe(false);
  });

  it.each(['TRUE', 'True', '1', 'yes', ' true', 'false '])('refuses the typo %j', (value) => {
    expect(() => parseProtectedMode(value)).toThrow(PROTECTED_MODE_ERROR);
  });
});
