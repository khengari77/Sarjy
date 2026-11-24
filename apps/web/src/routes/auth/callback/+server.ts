import { error, redirect, type Cookies, type RequestHandler } from '@sveltejs/kit';
import { exchangeOAuthCode } from '../../../lib/auth/account.server';
import { accountMode } from '../../../lib/auth/guard.server';
import { OAUTH_COOKIE, OAUTH_COOKIE_OPTIONS, OAUTH_NEXT_COOKIE, parseOAuthCookie } from '../../../lib/auth/oauth';
import { safeNextPath } from '../../../lib/auth/redirect';

const FAILED = '/sign-in?oauth=failed';

/** The PKCE material is single-use, so every callback path expires both cookies. */
function clearOAuth(cookies: Cookies): void {
  cookies.delete(OAUTH_COOKIE, OAUTH_COOKIE_OPTIONS);
  cookies.delete(OAUTH_NEXT_COOKIE, OAUTH_COOKIE_OPTIONS);
}

export const GET: RequestHandler = async ({ url, cookies, getClientAddress }) => {
  if (!accountMode) throw error(404, 'Not found');
  const pending = parseOAuthCookie(cookies.get(OAUTH_COOKIE));
  const next = safeNextPath(cookies.get(OAUTH_NEXT_COOKIE));
  clearOAuth(cookies);
  const code = url.searchParams.get('code');
  if (url.searchParams.has('error') || !code || !pending) throw redirect(303, FAILED);
  const failure = await exchangeOAuthCode(code, pending.verifier, { cookies, source: getClientAddress() });
  if (failure === null) throw redirect(303, next);
  const outcome =
    failure === 'ACCOUNT_ATTEMPT_LIMIT'
      ? 'limit'
      : failure === 'ACCOUNT_SERVICE_UNAVAILABLE'
        ? 'unavailable'
        : 'failed';
  throw redirect(303, `/sign-in?oauth=${outcome}`);
};
