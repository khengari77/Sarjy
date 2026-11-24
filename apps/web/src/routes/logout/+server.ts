import type { RequestHandler } from './$types';
import { leaveAccount } from '../../lib/auth/account.server';
import { accountMode, clearReviewerIdentity, clearSession } from '../../lib/auth/guard.server';

/** Clear the session. Both methods are safe to repeat. */
const logout: RequestHandler = async ({ cookies }) => {
  if (accountMode) await leaveAccount(cookies);
  clearSession(cookies);
  clearReviewerIdentity(cookies);
  return new Response(null, {
    status: 303,
    headers: {
      location: accountMode ? '/sign-in' : '/',
      'cache-control': 'no-store',
      'referrer-policy': 'no-referrer',
    },
  });
};

/** Another site can make a browser follow a link; with accounts only the form signs out. */
export const GET: RequestHandler = (event) =>
  accountMode ? new Response(null, { status: 303, headers: { location: '/' } }) : logout(event);
export const POST = logout;
