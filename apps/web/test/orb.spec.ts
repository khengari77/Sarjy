import { expect, test } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('sarjy-language', 'en'));
});

test('orb is a single keyboard-operable control that starts one session', async ({ page }) => {
  let sessionPosts = 0;
  await page.route('**/api/v1/voice/sessions', async (route) => {
    sessionPosts += 1;
    await route.fulfill({ status: 503, json: { detail: { code: 'PIPELINE_NOT_CONFIGURED' } } });
  });

  await page.goto('/');
  const orb = page.getByRole('button', { name: 'Start voice conversation' });
  await expect(orb).toBeVisible();
  await expect(orb).toHaveAttribute('type', 'button');
  await expect(orb).toHaveAttribute('data-phase', 'idle');
  await expect(orb).toBeEnabled();
  // The component renders statically on the server; wait for hydration before
  // sending keyboard input so the click handler is attached.
  await expect(orb).not.toHaveAttribute('data-renderer', 'static');

  // Bootstrap briefly disables the control; retry until focus sticks.
  await expect(async () => {
    await orb.focus();
    await expect(orb).toBeFocused({ timeout: 500 });
  }).toPass({ timeout: 10000 });
  await page.keyboard.press('Enter');

  await expect(page.getByRole('alert')).toHaveText('PIPELINE_NOT_CONFIGURED');
  expect(sessionPosts).toBe(1);
});

test('orb honours reduced motion with a static renderer', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  const orb = page.getByRole('button', { name: 'Start voice conversation' });
  await expect(orb).toBeVisible();
  await expect(orb).toHaveAttribute('data-renderer', 'static');
  await expect(orb.locator('canvas')).toHaveCount(0);
  await expect(orb.locator('.orb-static')).toHaveCount(1);
});

test('orb uses the enhanced renderer on capable browsers', async ({ page }) => {
  await page.goto('/');
  const orb = page.getByRole('button', { name: 'Start voice conversation' });
  await expect(orb).toBeVisible();
  await expect(orb).not.toHaveAttribute('data-renderer', 'static');
});
