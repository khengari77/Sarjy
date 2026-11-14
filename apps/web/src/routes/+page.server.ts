import { fail, redirect } from '@sveltejs/kit';
import type { Actions } from './$types';
import { accessFailures, issueSession, protectedMode, redeem } from '../lib/auth/guard.server';
import { safeNextPath } from '../lib/auth/redirect';

/**
 * Plain form fallback for the token door. The `?token=` link is the primary
 * path; this action keeps the page working with JavaScript disabled.
 */
export const actions: Actions = {
  default: async ({ request, cookies, url, getClientAddress, setHeaders }) => {
    setHeaders({ 'cache-control': 'no-store', 'referrer-policy': 'no-referrer' });
    if (!protectedMode) throw redirect(303, '/');

    const form = await request.formData();
    const submitted = form.get('token');
    const next = safeNextPath(form.get('next')?.toString() ?? url.searchParams.get('next'));
    const address = getClientAddress();
    const failure = { error: true, next };
    const reject = () => fail(400, failure);

    if (accessFailures.blocked(address)) return reject();
    const candidate = typeof submitted === 'string' ? submitted.trim() : '';
    if (!candidate) {
      accessFailures.fail(address);
      return reject();
    }

    const result = await redeem(candidate);
    if (!result.ok) {
      accessFailures.fail(address);
      return reject();
    }
    accessFailures.reset(address);
    if (!issueSession(cookies, result)) {
      accessFailures.fail(address);
      return reject();
    }
    throw redirect(303, next);
  },
};
