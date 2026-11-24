/**
 * OAuth entry policy with no environment or network: provider allowlist, PKCE
 * material and the authorize URL. Server wiring lives in the routes.
 */
export const SUPPORTED_OAUTH_PROVIDERS = ['google', 'apple', 'github', 'azure', 'facebook'] as const;

export type OAuthProvider = (typeof SUPPORTED_OAUTH_PROVIDERS)[number];

export const OAUTH_PROVIDER_LABELS: Record<OAuthProvider, string> = {
  google: 'Google',
  apple: 'Apple',
  github: 'GitHub',
  azure: 'Microsoft',
  facebook: 'Facebook',
};

export const OAUTH_COOKIE = 'sarjy_oauth';
export const OAUTH_NEXT_COOKIE = 'sarjy_oauth_next';
export const OAUTH_COOKIE_OPTIONS = {
  path: '/auth',
  httpOnly: true,
  secure: true,
  sameSite: 'lax',
  maxAge: 600,
} as const;

const OAUTH_COOKIE_SHAPE = new RegExp(`^(${SUPPORTED_OAUTH_PROVIDERS.join('|')})\\.([A-Za-z0-9_-]{43,128})$`);

function isSupported(value: string): value is OAuthProvider {
  return (SUPPORTED_OAUTH_PROVIDERS as readonly string[]).includes(value);
}

/** Comma-separated provider names; unknown names fail boot rather than disappear. */
export function parseOAuthProviders(value: string | null | undefined): OAuthProvider[] {
  if (!value) return [];
  const providers: OAuthProvider[] = [];
  for (const raw of value.split(',')) {
    const name = raw.trim().toLowerCase();
    if (!name) continue;
    if (!isSupported(name)) throw new Error(`OAUTH_PROVIDERS contains an unsupported provider: ${name}`);
    if (!providers.includes(name)) providers.push(name);
  }
  return providers;
}

function base64url(bytes: Uint8Array): string {
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/** A fresh 43-character PKCE verifier from 32 random bytes. */
export function newVerifier(): string {
  return base64url(crypto.getRandomValues(new Uint8Array(32)));
}

/** The S256 challenge for a verifier, base64url without padding. */
export async function challengeFor(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier));
  return base64url(new Uint8Array(digest));
}

export function authorizeUrl(options: {
  supabaseUrl: string;
  provider: OAuthProvider;
  redirectTo: string;
  challenge: string;
}): string {
  const base = options.supabaseUrl.replace(/\/+$/, '');
  const query = new URLSearchParams({
    provider: options.provider,
    redirect_to: options.redirectTo,
    code_challenge: options.challenge,
    code_challenge_method: 's256',
  });
  return `${base}/auth/v1/authorize?${query}`;
}

/** Split the PKCE cookie, accepting only an allowlisted provider and a well-formed verifier. */
export function parseOAuthCookie(
  value: string | null | undefined,
): { provider: OAuthProvider; verifier: string } | null {
  if (!value) return null;
  const match = OAUTH_COOKIE_SHAPE.exec(value);
  if (!match) return null;
  return { provider: match[1] as OAuthProvider, verifier: match[2] };
}
