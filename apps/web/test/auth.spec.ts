import { expect, test } from '@playwright/test';
import { readFile, rm } from 'node:fs/promises';
import { resolve } from 'node:path';
import { signSession, tokenFingerprint } from '../src/lib/auth/session';

const BASE = 'http://localhost:4176';
const TOKEN = 'test-review-token-0123456789abcdef';
const SESSION_SECRET = 'test-session-secret-0123456789abcdef';
const STATE = resolve('test-results/auth-state.json');

async function exchange(token = TOKEN, init: RequestInit = {}): Promise<Response> {
  return fetch(`${BASE}/?token=${encodeURIComponent(token)}`, { redirect: 'manual', ...init });
}

async function used(): Promise<number> {
  try {
    const state = JSON.parse(await readFile(STATE, 'utf8')) as Record<string, { used: number }>;
    const first = Object.values(state)[0];
    return first?.used ?? 0;
  } catch {
    return 0;
  }
}

test.beforeEach(async () => {
  await rm(STATE, { force: true });
});

test('unauthenticated / is a token door with no app content or cookie', async () => {
  const response = await fetch(`${BASE}/`, { redirect: 'manual' });
  expect(response.status).toBe(200);
  const body = await response.text();
  expect(body).toContain('id="access-token"');
  expect(body).toContain('role="alert"');
  expect(body).not.toContain('id="voice-stage"');
  expect(body).not.toContain('Conversations');
  expect(response.headers.get('set-cookie')).toBeNull();
  expect(response.headers.get('referrer-policy')).toBe('same-origin');
  expect(response.headers.get('cache-control')).toBe('no-store');
});

test('a wrong token shows a generic error and sets no cookie', async () => {
  const response = await exchange('definitely-wrong');
  expect(response.status).toBe(200);
  const body = await response.text();
  expect(body).toContain('That link is not valid.');
  expect(response.headers.get('set-cookie')).toBeNull();
});

test('a valid token redirects without the token and sets a signed session', async () => {
  const response = await exchange();
  expect(response.status).toBe(303);
  expect(response.headers.get('location')).toBe('/');
  expect(response.headers.get('referrer-policy')).toBe('no-referrer');
  expect(response.headers.get('cache-control')).toBe('no-store');
  const cookie = response.headers.getSetCookie()[0] ?? '';
  expect(cookie).toContain('sarjy_review_session=');
  expect(cookie).toMatch(/HttpOnly/i);
  expect(cookie).toMatch(/Secure/i);
  expect(cookie).toMatch(/SameSite=Lax/i);
  expect(cookie).toMatch(/Path=\//i);
  expect(cookie).toMatch(/Max-Age=\d+/i);
});

test('the token lands in the app and leaves no token in the URL', async ({ browser }) => {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto(`${BASE}/?token=${TOKEN}`);
  await expect(page).toHaveURL(`${BASE}/`);
  await expect(page.locator('#voice-stage')).toBeVisible();
  const session = (await context.cookies()).find((cookie) => cookie.name === 'sarjy_review_session');
  expect(session).toMatchObject({ httpOnly: true, secure: true, sameSite: 'Lax', path: '/' });
  expect(await used()).toBe(1);

  // Revisiting with the cookie must not consume another admission.
  await page.reload();
  await expect(page.locator('#voice-stage')).toBeVisible();
  expect(await used()).toBe(1);
  await context.close();
});

test('a tampered cookie is treated as no session', async ({ browser }) => {
  const response = await exchange();
  const value = (response.headers.getSetCookie()[0] ?? '').split(';')[0].split('=')[1];
  const tampered = `${value.slice(0, -1)}${value.endsWith('A') ? 'B' : 'A'}`;
  const context = await browser.newContext();
  await context.addCookies([{ name: 'sarjy_review_session', value: tampered, url: BASE }]);
  const page = await context.newPage();
  await page.goto(`${BASE}/`);
  await expect(page.locator('#access-token')).toBeVisible();
  await expect(page.locator('#voice-stage')).toHaveCount(0);
  await context.close();
});

test('an expired session cookie is treated as no session', async ({ browser }) => {
  const fingerprint = tokenFingerprint(TOKEN, SESSION_SECRET);
  const signed = signSession(SESSION_SECRET, {
    fingerprint,
    expiresAt: Date.now() - 1_000,
    now: Date.now() - 7_200_000,
  });
  expect(signed).not.toBeNull();
  const context = await browser.newContext();
  await context.addCookies([{ name: 'sarjy_review_session', value: signed!.token, url: BASE }]);
  const page = await context.newPage();
  await page.goto(`${BASE}/`);
  await expect(page.locator('#access-token')).toBeVisible();
  await context.close();
});

test('N exchanges succeed and N+1 is refused', async () => {
  const first = await exchange();
  const second = await exchange();
  const third = await exchange();
  expect(first.status).toBe(303);
  expect(second.status).toBe(303);
  expect(third.status).toBe(200);
  expect(third.headers.get('set-cookie')).toBeNull();
  expect(await third.text()).toContain('That link is not valid.');
  expect(await used()).toBe(2);
});

test('concurrent exchanges at the last use admit exactly one browser', async () => {
  await exchange();
  const responses = await Promise.all(Array.from({ length: 8 }, () => exchange()));
  const statuses = responses.map((response) => response.status);
  expect(statuses.filter((status) => status === 303)).toHaveLength(1);
  expect(statuses.filter((status) => status === 200)).toHaveLength(7);
  expect(await used()).toBe(2);
});

test('next is restricted to same-origin paths', async () => {
  const response = await fetch(`${BASE}/?token=${TOKEN}&next=${encodeURIComponent('https://evil.example')}`, {
    redirect: 'manual',
  });
  expect(response.status).toBe(303);
  expect(response.headers.get('location')).toBe('/');
});

test('a token on a non-root path never appears in the next URL', async () => {
  const response = await fetch(`${BASE}/some/page?token=${TOKEN}&keep=1`, {
    redirect: 'manual',
    headers: { accept: 'text/html' },
  });
  expect(response.status).toBe(303);
  const location = response.headers.get('location') ?? '';
  expect(location).not.toContain(TOKEN);
  expect(location).toBe(`/?next=${encodeURIComponent('/some/page?keep=1')}`);
});

test('logout clears the session and returns to the door', async ({ browser }) => {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto(`${BASE}/?token=${TOKEN}`);
  await expect(page.locator('#voice-stage')).toBeVisible();
  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page).toHaveURL(`${BASE}/`);
  await expect(page.locator('#access-token')).toBeVisible();
  expect((await context.cookies()).some((cookie) => cookie.name === 'sarjy_review_session')).toBe(false);
  await context.close();
});

test('the form is labelled and keyboard-operable', async ({ browser }) => {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto(`${BASE}/`);
  await page.keyboard.press('Tab');
  await expect(page.getByLabel('Access token')).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('button', { name: 'Enter' })).toBeFocused();
  await context.close();
});

test('the door works with JavaScript disabled', async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  const page = await context.newPage();
  await page.goto(`${BASE}/`);
  await page.getByLabel('Access token').fill(TOKEN);
  await page.getByRole('button', { name: 'Enter' }).click();
  await expect(page).toHaveURL(`${BASE}/`);
  await expect(page.locator('#voice-stage')).toBeVisible();
  await context.close();
});

test('repeated failures return a distinct wait message', async () => {
  let body = '';
  for (let attempt = 0; attempt < 14; attempt += 1) {
    const response = await exchange('definitely-wrong');
    body = await response.text();
    if (body.includes('Too many attempts.')) break;
  }
  expect(body).toContain('Too many attempts. Try again shortly.');
});
