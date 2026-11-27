import { expect, test, type Page } from '@playwright/test';

// Runs under playwright.account.config.ts: account pages are public, and the
// API is unreachable, so only the form's client behaviour is exercised.

test.beforeEach(async ({ page }) => {
  test.setTimeout(90_000);
  page.setDefaultTimeout(20_000);
});

/** Click until Svelte has hydrated and the toggle actually flips the input. */
async function waitForHydratedPasswordToggle(page: Page): Promise<void> {
  const password = page.locator('#account-password');
  const toggle = page.getByRole('button', { name: 'Show password' });
  await expect(async () => {
    await toggle.click();
    await expect(password).toHaveAttribute('type', 'text', { timeout: 1_000 });
  }).toPass({ timeout: 15_000 });
  await page.getByRole('button', { name: 'Hide password' }).click();
  await expect(password).toHaveAttribute('type', 'password');
}

test('account pages keep their own single title and no skip link', async ({ page }) => {
  await page.goto('/sign-in');
  expect(await page.title()).toBe('Sarjy — Sign in');
  expect(await page.locator('title').count()).toBe(1);
  await expect(page.getByRole('link', { name: 'Skip to voice' })).toHaveCount(0);

  await page.goto('/sign-up');
  expect(await page.title()).toBe('Sarjy — Create your account');
  expect(await page.locator('title').count()).toBe(1);
  await expect(page.getByRole('link', { name: 'Skip to voice' })).toHaveCount(0);
});

test('the sign-in form shows a pending state and a show/hide password toggle', async ({ page }) => {
  await page.goto('/sign-in');

  const password = page.locator('#account-password');
  await expect(password).toHaveAttribute('type', 'password');
  const toggle = page.getByRole('button', { name: 'Show password' });
  await expect(toggle).toHaveAttribute('aria-pressed', 'false');
  const box = await toggle.boundingBox();
  expect(box, 'show-password button').not.toBeNull();
  expect(box!.width).toBeGreaterThanOrEqual(44);
  expect(box!.height).toBeGreaterThanOrEqual(44);

  await waitForHydratedPasswordToggle(page);

  // Hold the enhanced form submission open so the pending label is observable.
  await page.route('**/sign-in**', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.fallback();
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 2_000));
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ type: 'failure', status: 400, data: { message: 'nope' } }),
    });
  });

  await page.getByLabel('Email').fill('reviewer@example.com');
  await password.fill('correct-horse');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('button', { name: 'Signing in...' })).toBeDisabled();
});

test('the sign-up form reports that an account is being created', async ({ page }) => {
  await page.goto('/sign-up');
  await waitForHydratedPasswordToggle(page);

  await page.route('**/sign-up**', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.fallback();
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 2_000));
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ type: 'failure', status: 400, data: { message: 'nope' } }),
    });
  });

  await page.getByLabel('Email').fill('reviewer@example.com');
  await page.locator('#account-password').fill('correct-horse');
  await page.getByRole('button', { name: 'Create your account' }).click();
  await expect(page.getByRole('button', { name: 'Creating account...' })).toBeDisabled();
});
