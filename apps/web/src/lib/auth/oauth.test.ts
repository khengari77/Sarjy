import { describe, expect, it } from 'vitest';
import {
  OAUTH_COOKIE_OPTIONS,
  authorizeUrl,
  challengeFor,
  newVerifier,
  parseOAuthCookie,
  parseOAuthProviders,
} from './oauth';

describe('OAuth providers', () => {
  it('accepts the allowlist once, in order, after trimming and lowercasing', () => {
    expect(parseOAuthProviders(' Google,github , GOOGLE,azure,facebook,apple ')).toEqual([
      'google',
      'github',
      'azure',
      'facebook',
      'apple',
    ]);
  });

  it('treats an unset or empty list as disabled', () => {
    expect(parseOAuthProviders(undefined)).toEqual([]);
    expect(parseOAuthProviders('')).toEqual([]);
    expect(parseOAuthProviders(' , ')).toEqual([]);
  });

  it('refuses an unknown provider rather than dropping it', () => {
    for (const value of ['twitter', 'google,twitter', 'microsoft']) {
      expect(() => parseOAuthProviders(value)).toThrow('OAUTH_PROVIDERS');
    }
  });
});

describe('PKCE', () => {
  it('matches the RFC 7636 S256 test vector', async () => {
    await expect(challengeFor('dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk')).resolves.toBe(
      'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM',
    );
  });

  it('makes a fresh, well-formed verifier', () => {
    const first = newVerifier();
    const second = newVerifier();
    expect(first).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(second).not.toBe(first);
  });
});

describe('authorizeUrl', () => {
  it('builds the Supabase PKCE authorize URL', () => {
    const url = new URL(
      authorizeUrl({
        supabaseUrl: 'https://project.supabase.co',
        provider: 'google',
        redirectTo: 'https://sarjy.example/auth/callback',
        challenge: 'challenge-value',
      }),
    );
    expect(`${url.origin}${url.pathname}`).toBe('https://project.supabase.co/auth/v1/authorize');
    expect(Object.fromEntries(url.searchParams)).toEqual({
      provider: 'google',
      redirect_to: 'https://sarjy.example/auth/callback',
      code_challenge: 'challenge-value',
      code_challenge_method: 's256',
    });
  });

  it('does not double the slash after a trailing Supabase URL', () => {
    const url = authorizeUrl({
      supabaseUrl: 'https://project.supabase.co/',
      provider: 'github',
      redirectTo: 'https://sarjy.example/auth/callback',
      challenge: 'challenge-value',
    });
    expect(url.startsWith('https://project.supabase.co/auth/v1/authorize?')).toBe(true);
  });
});

describe('OAuth cookie', () => {
  it('accepts only an allowlisted provider with a well-formed verifier', () => {
    expect(parseOAuthCookie(`google.${'a'.repeat(43)}`)).toEqual({ provider: 'google', verifier: 'a'.repeat(43) });
    for (const bad of [
      undefined,
      '',
      `twitter.${'a'.repeat(43)}`,
      `google.${'a'.repeat(42)}`,
      `google.${'a'.repeat(129)}`,
      `google.${'a'.repeat(42)}!`,
      `${'a'.repeat(43)}`,
    ]) {
      expect(parseOAuthCookie(bad)).toBeNull();
    }
  });

  it('pins the short-lived secure cookie attributes', () => {
    expect(OAUTH_COOKIE_OPTIONS).toEqual({
      path: '/auth',
      httpOnly: true,
      secure: true,
      sameSite: 'lax',
      maxAge: 600,
    });
  });
});
