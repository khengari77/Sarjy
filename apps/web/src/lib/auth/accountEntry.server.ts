import { error, fail, redirect, type RequestEvent } from '@sveltejs/kit';
import { OAUTH_PROVIDERS } from '$app/env/private';
import { accountMessage, oauthMessage, type AccountAction } from './account';
import { accountSession, enterAccount } from './account.server';
import { accountMode } from './guard.server';
import { parseOAuthProviders } from './oauth';
import { safeNextPath } from './redirect';

const providers = parseOAuthProviders(OAUTH_PROVIDERS);

/** The load and form action shared by the sign-in and sign-up pages. */
export function accountEntry(action: AccountAction) {
  return {
    load: async ({ cookies, request, url }: Pick<RequestEvent, 'cookies' | 'request' | 'url'>) => {
      if (!accountMode) throw error(404, 'Not found');
      const next = safeNextPath(url.searchParams.get('next'));
      const message = oauthMessage(url.searchParams.get('oauth'));
      // After a failed form this load runs again: show the failure, whoever was signed in before.
      if (request.method === 'POST') return { next, providers, message };
      if ((await accountSession(cookies)) === 'valid') throw redirect(303, next);
      return { next, providers, message };
    },
    submit: async ({ request, cookies, getClientAddress }: RequestEvent) => {
      if (!accountMode) throw error(404, 'Not found');
      const form = await request.formData();
      const text = (name: string) => {
        const value = form.get(name);
        return typeof value === 'string' ? value : '';
      };
      const email = text('email').trim();
      const next = safeNextPath(text('next'));
      const code = await enterAccount(
        action,
        { email, password: text('password') },
        { cookies, source: getClientAddress() },
      );
      if (code === null) throw redirect(303, next);
      const status = code === 'ACCOUNT_ATTEMPT_LIMIT' ? 429 : code === 'ACCOUNT_SERVICE_UNAVAILABLE' ? 503 : 400;
      // The password is never sent back to the page.
      return fail(status, { message: accountMessage(code), email: email.slice(0, 254), next });
    },
  };
}
