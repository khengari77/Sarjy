import { expect, test } from '@playwright/test';
import { access, rm } from 'node:fs/promises';
import { resolve } from 'node:path';
import { signSession, tokenFingerprint } from '../src/lib/auth/session';

const BASE = 'http://localhost:4178';
const TOKEN = 'test-review-token-0123456789abcdef';
const SESSION_SECRET = 'test-session-secret-0123456789abcdef';
const STATE = resolve('test-results/auth-expired-state.json');

test('past REVIEW_ACCESS_EXPIRES_AT the token and valid-signature sessions are refused', async ({ browser }) => {
  await rm(STATE, { force: true });

  const exchange = await fetch(`${BASE}/?token=${encodeURIComponent(TOKEN)}`, { redirect: 'manual' });
  expect(exchange.status).toBe(200);
  expect(exchange.headers.get('set-cookie')).toBeNull();
  expect(await exchange.text()).toContain('That link is not valid.');

  // A cookie with a valid signature but an instant beyond the authoritative
  // `REVIEW_ACCESS_EXPIRES_AT` must still be treated as absent.
  const signed = signSession(SESSION_SECRET, {
    fingerprint: tokenFingerprint(TOKEN, SESSION_SECRET),
    expiresAt: Date.now() + 3_600_000,
  });
  expect(signed).not.toBeNull();
  const context = await browser.newContext();
  await context.addCookies([{ name: 'sarjy_review_session', value: signed!.token, url: BASE }]);
  const page = await context.newPage();
  await page.goto(`${BASE}/`);
  await expect(page.locator('#access-token')).toBeVisible();
  await expect(page.locator('#voice-stage')).toHaveCount(0);
  await context.close();

  // The expired exchange was rejected before accounting, so no counter exists.
  await expect(access(STATE)).rejects.toThrow();
});
