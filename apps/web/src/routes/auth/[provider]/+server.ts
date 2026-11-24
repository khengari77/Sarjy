import { error, type RequestHandler } from '@sveltejs/kit';
import { OAUTH_PROVIDERS, SUPABASE_URL } from '$app/env/private';
import { accountMode } from '../../../lib/auth/guard.server';
import {
  OAUTH_COOKIE,
  OAUTH_COOKIE_OPTIONS,
  OAUTH_NEXT_COOKIE,
  authorizeUrl,
  challengeFor,
  newVerifier,
  parseOAuthProviders,
} from '../../../lib/auth/oauth';
import { safeNextPath } from '../../../lib/auth/redirect';

const providers = parseOAuthProviders(OAUTH_PROVIDERS);

export const GET: RequestHandler = async ({ params, url, cookies }) => {
  const provider = providers.find((name) => name === params.provider);
  if (!accountMode || !provider || !SUPABASE_URL) throw error(404, 'Not found');
  const verifier = newVerifier();
  const challenge = await challengeFor(verifier);
  const origin = (process.env.ORIGIN || url.origin).replace(/\/+$/, '');
  cookies.set(OAUTH_COOKIE, `${provider}.${verifier}`, OAUTH_COOKIE_OPTIONS);
  cookies.set(OAUTH_NEXT_COOKIE, safeNextPath(url.searchParams.get('next')), OAUTH_COOKIE_OPTIONS);
  // `redirect()` refuses another origin, so answer with the redirect itself.
  const location = authorizeUrl({
    supabaseUrl: SUPABASE_URL,
    provider,
    redirectTo: `${origin}/auth/callback`,
    challenge,
  });
  return new Response(null, {
    status: 303,
    headers: { location, 'cache-control': 'no-store', 'referrer-policy': 'no-referrer' },
  });
};
