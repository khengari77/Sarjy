import { expect, test } from '@playwright/test';

test('English smoke selectors reach real controls without starting a call', async ({ page }) => {
  const admissions: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().endsWith('/api/v1/voice/sessions')) admissions.push(request.url());
  });
  await page.addInitScript(() => localStorage.setItem('sarjy-language', 'en'));
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Start call', exact: true })).toBeVisible();
  const settings = page.getByRole('button', { name: 'Show memory and settings', exact: true });
  if ((await settings.getAttribute('aria-expanded')) !== 'true') await settings.click();
  await expect(page.getByRole('button', { name: 'Load voices / Search', exact: true })).toBeVisible();
  await expect(page.getByLabel('Choose voice', { exact: true })).toBeVisible();
  expect(admissions).toEqual([]);
});
