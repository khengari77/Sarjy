import { describe, expect, it } from 'vitest';
import { PROTECTED_MODE_ERROR, isLocalOrigin, parseProtectedMode } from './config';

describe('DEVELOPMENT_PROTECTED', () => {
  it('accepts the two documented values', () => {
    expect(parseProtectedMode('true')).toBe(true);
    expect(parseProtectedMode('false')).toBe(false);
  });

  it('treats an unset variable as the documented unprotected default', () => {
    expect(parseProtectedMode(undefined)).toBe(false);
  });

  it.each(['TRUE', '1', 'yes', ' true', '', 'false '])('refuses the ambiguous value %j', (value) => {
    expect(() => parseProtectedMode(value)).toThrow(PROTECTED_MODE_ERROR);
  });
});

describe('explicit protected mode for remote origins', () => {
  it('treats local and unset origins as development', () => {
    expect(isLocalOrigin(undefined)).toBe(true);
    expect(isLocalOrigin('http://localhost:5173')).toBe(true);
    expect(isLocalOrigin('http://127.0.0.1:3000')).toBe(true);
  });

  it('does not treat a remote origin as local', () => {
    expect(isLocalOrigin('https://sarjy.example')).toBe(false);
    expect(isLocalOrigin('not a url')).toBe(false);
  });
});
