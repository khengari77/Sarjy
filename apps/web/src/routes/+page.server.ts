import { fail, redirect } from '@sveltejs/kit';
import type { Actions } from './$types';
import { accessFailures, issueSession, protectedMode, redeem } from '../lib/auth/guard.server';
import { tokenKey } from '../lib/auth/access-state';
import { failureKey } from '../lib/auth/rate-limit';
import { safeNextPath } from '../lib/auth/redirect';

/**
 * Plain form fallback for the token door. The `?token=` link is the primary
 * path; this action keeps the page working with JavaScript disabled.
 */
export const actions: Actions = {
  default: async ({ request, cookies, url, getClientAddress, setHeaders }) => {
    setHeaders({ 'cache-control': 'no-store' });
    if (!protectedMode) throw redirect(303, '/');

    const form = await request.formData();
    const submitted = form.get('token');
    const next = safeNextPath(form.get('next')?.toString() ?? url.searchParams.get('next'));
    const address = failureKey(request, getClientAddress());
    const candidate = typeof submitted === 'string' ? submitted.trim() : '';
    const tokenId = tokenKey(candidate);
    const reject = (blocked = false) => fail(blocked ? 429 : 400, { error: true, blocked, next });

    if (accessFailures.blocked(address, tokenId)) return reject(true);
    if (!candidate) {
      accessFailures.fail(address, tokenId);
      return reject();
    }

    const result = await redeem(candidate);
    if (!result.ok) {
      // Missing storage is not a failed guess; never spend the visitor's
      // failure budget on an internal outage.
      if (result.reason !== 'unavailable') accessFailures.fail(address, tokenId);
      return reject();
    }
    accessFailures.reset(address, tokenId);
    if (!issueSession(cookies, result)) return reject();
    throw redirect(303, next);
  },
};
