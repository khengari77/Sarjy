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
  await expect(page.locator('.orb-button')).toBeVisible();
  // The shell is server-rendered before it hydrates; clicking a trigger early is a
  // no-op and the closed drawer stays inert (absent from the a11y tree), so wait for
  // hydration before driving it.
  await page.waitForFunction(() => document.querySelector('[data-hydrated="true"]') !== null);
  const settings = page.getByRole('button', { name: 'Show memory and settings', exact: true });
  await expect
    .poll(async () => {
      if ((await settings.getAttribute('aria-expanded')) !== 'true') await settings.click();
      return settings.getAttribute('aria-expanded');
    })
    .toBe('true');
  await expect(page.getByRole('button', { name: 'Load voices / Search', exact: true })).toBeVisible();
  await expect(page.getByLabel('Choose voice', { exact: true })).toBeVisible();
  const composer = page.getByRole('textbox', { name: 'Write a message…' });
  await expect(composer).toBeEnabled();
  await composer.fill('smoke selector check');
  await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeEnabled();
  expect(admissions).toEqual([]);
});
