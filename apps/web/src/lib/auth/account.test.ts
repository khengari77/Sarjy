import { describe, expect, it } from 'vitest';
import { ACCOUNT_UNAVAILABLE, accountMessage, isAccountPublicPath, issuedSession, oauthMessage } from './account';
import { parseAccountMode } from './config';

const TOKEN = 'a'.repeat(43);

describe('account policy', () => {
  it('opens the two entry pages and the allowlisted OAuth paths', () => {
    expect(
      [
        '/sign-in',
        '/sign-up',
        '/auth/callback',
        '/auth/google',
        '/auth/apple',
        '/auth/github',
        '/auth/azure',
        '/auth/facebook',
      ].every(isAccountPublicPath),
    ).toBe(true);
    for (const path of [
      '/',
      '/sign-in/x',
      '/sign-inx',
      '/Sign-in',
      '/review',
      '//sign-in',
      '/auth/twitter',
      '/auth/',
      '/auth/google/x',
      '/auth/callback/',
    ]) {
      expect(isAccountPublicPath(path)).toBe(false);
    }
  });

  it('never shows an unknown code', () => {
    expect(accountMessage('ACCOUNT_EXISTS')).toContain('already exists');
    expect(accountMessage('ACCOUNT_OAUTH_FAILED')).toContain('did not complete');
    for (const code of ['boom', 'toString', '__proto__', undefined, 7]) {
      expect(accountMessage(code)).toBe(ACCOUNT_UNAVAILABLE);
    }
  });

  it('maps OAuth outcomes to entry-page copy', () => {
    expect(oauthMessage('failed')).toBe(accountMessage('ACCOUNT_OAUTH_FAILED'));
    expect(oauthMessage('taken')).toBe(accountMessage('ACCOUNT_OAUTH_EMAIL_TAKEN'));
    expect(oauthMessage('unavailable')).toBe(ACCOUNT_UNAVAILABLE);
    expect(oauthMessage('limit')).toContain('Too many attempts');
    for (const value of ['', 'boom', 'toString', '__proto__', undefined, 7]) {
      expect(oauthMessage(value)).toBe('');
    }
  });

  it('accepts exactly one well-formed session cookie', () => {
    const good = `sarjy_account=${TOKEN}; HttpOnly; Max-Age=2592000; Path=/; SameSite=lax; Secure`;
    expect(issuedSession(['other=1', good])).toEqual({ token: TOKEN, maxAge: 2592000 });
    for (const bad of [
      [],
      [good, good],
      [`sarjy_account=${TOKEN}`],
      [`sarjy_account=${TOKEN}; Max-Age=0`],
      ['sarjy_account=""; Max-Age=0'],
      [`sarjy_account=${TOKEN}x; Max-Age=5`],
      [`xsarjy_account=${TOKEN}; Max-Age=5`],
    ]) {
      expect(issuedSession(bad)).toBeNull();
    }
  });

  it('refuses a misspelled switch', () => {
    expect(parseAccountMode('true')).toBe(true);
    expect([parseAccountMode('false'), parseAccountMode(undefined)]).toEqual([false, false]);
    for (const value of ['', 'TRUE', '1', 'yes'])
      expect(() => parseAccountMode(value)).toThrow('ACCOUNT_ACCESS_ENABLED');
  });
});
