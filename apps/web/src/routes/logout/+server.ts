import type { RequestHandler } from './$types';
import { clearReviewerIdentity, clearSession } from '../../lib/auth/guard.server';

/** Clear the reviewer session. Both methods are safe to repeat. */
const logout: RequestHandler = ({ cookies }) => {
  clearSession(cookies);
  clearReviewerIdentity(cookies);
  return new Response(null, {
    status: 303,
    headers: { location: '/', 'cache-control': 'no-store', 'referrer-policy': 'no-referrer' },
  });
};

export const GET = logout;
export const POST = logout;
