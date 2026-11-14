import { redirect } from '@sveltejs/kit';
import type { LayoutServerLoad } from './$types';
import { accessFailures, issueSession, protectedMode, redeem, sessionIsValid } from '../lib/auth/guard.server';
import { safeNextPath } from '../lib/auth/redirect';

/**
 * The gate lives in the root layout so both the entry page and every app page
 * see the same decision. Unauthenticated HTML requests are allowed through by
 * `hooks.server.ts`; this load serves the door and performs the `?token=`
 * exchange server-side, so a valid link lands in the app on the first paint.
 */
export const load: LayoutServerLoad = async ({ url, cookies, getClientAddress, setHeaders }) => {
  if (!protectedMode) return { entry: false, error: false, blocked: false, next: '/', protectedMode };

  const next = safeNextPath(url.searchParams.get('next'));
  const token = url.searchParams.get('token');
  const authenticated = sessionIsValid(cookies);
  if (token !== null || !authenticated) setHeaders({ 'cache-control': 'no-store' });

  if (authenticated) {
    // A valid session opening the link again must not consume a use; the
    // redirect also strips the token from the URL and history.
    if (token !== null) throw redirect(303, next);
    return { entry: false, error: false, blocked: false, next, protectedMode };
  }

  if (token === null) return { entry: true, error: false, blocked: false, next, protectedMode };

  const address = getClientAddress();
  const failure = (blocked: boolean) => ({ entry: true, error: true, blocked, next, protectedMode });
  if (accessFailures.blocked(address)) return failure(true);

  const result = await redeem(token);
  if (!result.ok) {
    // A storage outage is not the visitor's mistake and must not consume their
    // failure budget. Everything else is a failed guess.
    if (result.reason !== 'unavailable') accessFailures.fail(address);
    return failure(false);
  }
  accessFailures.reset(address);
  if (!issueSession(cookies, result)) return failure(false);
  throw redirect(303, next);
};
