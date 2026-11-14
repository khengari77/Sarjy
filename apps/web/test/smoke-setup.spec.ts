import { expect, test } from '@playwright/test';

test('English smoke selectors reach real controls without starting a call', async ({ page }) => {
  const admissions: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().endsWith('/api/v1/voice/sessions')) admissions.push(request.url());
  });
  await page.addInitScript(() => localStorage.setItem('sarjy-language', 'en'));
  await page.goto('/');
  // The drawer trigger is inert until Svelte hydrates; a click before then is
  // dropped and the drawer never opens. Wait for the hydrated renderer first.
  await expect(page.locator('.orb-button')).not.toHaveAttribute('data-renderer', 'static', { timeout: 30000 });
  await expect(page.getByRole('button', { name: 'Start call', exact: true })).toBeVisible();
  const settings = page.getByRole('button', { name: 'Show memory and settings', exact: true });
  if ((await settings.getAttribute('aria-expanded')) !== 'true') await settings.click();
  await expect(page.getByRole('button', { name: 'Load voices / Search', exact: true })).toBeVisible();
  await expect(page.getByLabel('Choose voice', { exact: true })).toBeVisible();
  const composer = page.getByRole('textbox', { name: 'Write a message…' });
  await expect(composer).toBeEnabled();
  await composer.fill('smoke selector check');
  await expect(page.getByRole('button', { name: 'Send ↗', exact: true })).toBeEnabled();
  expect(admissions).toEqual([]);
});
