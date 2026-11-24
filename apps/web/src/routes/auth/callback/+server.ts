import { error, redirect, type Cookies, type RequestHandler } from '@sveltejs/kit';
import { OAUTH_PROVIDERS } from '$app/env/private';
import { exchangeOAuthCode } from '../../../lib/auth/account.server';
import { accountMode } from '../../../lib/auth/guard.server';
import {
  OAUTH_COOKIE,
  OAUTH_COOKIE_OPTIONS,
  OAUTH_NEXT_COOKIE,
  parseOAuthCookie,
  parseOAuthProviders,
} from '../../../lib/auth/oauth';
import { safeNextPath } from '../../../lib/auth/redirect';

const providers = parseOAuthProviders(OAUTH_PROVIDERS);

/** The PKCE material is single-use, so every callback path expires both cookies. */
function clearOAuth(cookies: Cookies): void {
  cookies.delete(OAUTH_COOKIE, OAUTH_COOKIE_OPTIONS);
  cookies.delete(OAUTH_NEXT_COOKIE, OAUTH_COOKIE_OPTIONS);
}

function refusal(outcome: string, next: string): string {
  const destination = `/sign-in?oauth=${outcome}`;
  return next === '/' ? destination : `${destination}&next=${encodeURIComponent(next)}`;
}

export const GET: RequestHandler = async ({ url, cookies, getClientAddress }) => {
  if (!accountMode) throw error(404, 'Not found');
  const pending = parseOAuthCookie(cookies.get(OAUTH_COOKIE));
  const next = safeNextPath(cookies.get(OAUTH_NEXT_COOKIE));
  clearOAuth(cookies);
  const code = url.searchParams.get('code');
  if (url.searchParams.has('error') || !code || !pending || !providers.includes(pending.provider)) {
    throw redirect(303, refusal('failed', next));
  }
  const failure = await exchangeOAuthCode(code, pending.verifier, { cookies, source: getClientAddress() });
  if (failure === null) throw redirect(303, next);
  const outcome =
    failure === 'ACCOUNT_ATTEMPT_LIMIT'
      ? 'limit'
      : failure === 'ACCOUNT_SERVICE_UNAVAILABLE'
        ? 'unavailable'
        : failure === 'ACCOUNT_OAUTH_EMAIL_TAKEN'
          ? 'taken'
          : 'failed';
  throw redirect(303, refusal(outcome, next));
};
