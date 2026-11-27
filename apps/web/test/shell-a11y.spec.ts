import { expect, test, type Page } from '@playwright/test';
import { gotoHistory, mockHistory } from './support/historyHarness';

// UX-2 shell accessibility: overlay close controls, touch targets, mobile
// viewport metadata, a keyboard skip link and the renamed drawer tabs.

async function waitForShell(page: Page) {
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const hydrated = await page
      .waitForFunction(
        () =>
          Boolean(document.querySelector('.orb-button')) &&
          document.querySelector('.orb-button')?.getAttribute('data-renderer') !== 'static',
        null,
        { timeout: 12_000 },
      )
      .then(() => true)
      .catch(() => false);
    if (hydrated) return;
    await page.reload({ waitUntil: 'domcontentloaded' });
  }
  throw new Error('shell never hydrated');
}

test.beforeEach(async ({ page }) => {
  test.setTimeout(90_000);
  page.setDefaultTimeout(20_000);
  await page.addInitScript(() => localStorage.setItem('sarjy-language', 'en'));
});

test('an overlay drawer has a 44px close control that restores focus to its toggle', async ({ page }) => {
  await mockHistory(page);
  await page.setViewportSize({ width: 900, height: 720 });
  await gotoHistory(page);

  const toggle = page.getByRole('button', { name: 'Show memory and settings' });
  await toggle.click();
  const drawer = page.locator('#shell-right');
  await expect(drawer).toHaveAttribute('role', 'dialog');

  const close = drawer.getByRole('button', { name: 'Close' });
  await expect(close).toBeVisible();
  // It is the first focusable element, so opening the drawer lands here.
  await expect(close).toBeFocused();
  const box = await close.boundingBox();
  expect(box, 'close button has a layout box').not.toBeNull();
  expect(box!.width).toBeGreaterThanOrEqual(44);
  expect(box!.height).toBeGreaterThanOrEqual(44);

  await close.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(drawer).not.toBeVisible();
  await expect(toggle).toBeFocused();
});

test('the drawer tabs are renamed while their ARIA ids stay stable', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');
  await waitForShell(page);
  await page.getByRole('button', { name: 'Show memory and settings' }).click();

  const drawer = page.locator('#shell-right');
  await expect(drawer.getByRole('tab')).toHaveText(['Memory', 'Settings', 'Transcript']);
  await expect(page.locator('#drawer-tab-memories')).toHaveAttribute('aria-controls', 'drawer-panel-memories');
  await expect(page.locator('#drawer-tab-controls')).toHaveAttribute('aria-controls', 'drawer-panel-controls');
  await expect(page.locator('#drawer-tab-transcript')).toHaveAttribute('aria-controls', 'drawer-panel-transcript');
});

test('the row menu follows the menu button keyboard pattern', async ({ page }) => {
  await mockHistory(page);
  await gotoHistory(page);

  const trigger = page.getByRole('button', { name: 'Conversation actions for Trip to Paris' });
  const menu = page.getByRole('menu');

  // ArrowDown on the trigger opens the menu and moves focus to its first item.
  await trigger.focus();
  await page.keyboard.press('ArrowDown');
  await expect(menu).toBeVisible();
  const deleteItem = menu.getByRole('menuitem', { name: 'Delete' });
  await expect(deleteItem).toBeFocused();

  // Home/End/Arrow stay inside the menu rather than falling back to the page.
  for (const key of ['ArrowDown', 'ArrowUp', 'Home', 'End']) {
    await page.keyboard.press(key);
    await expect(deleteItem).toBeFocused();
  }

  await page.keyboard.press('Escape');
  await expect(menu).toHaveCount(0);
  await expect(trigger).toBeFocused();

  // Tab closes the menu without stranding focus on <body>.
  await trigger.click();
  await expect(deleteItem).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(menu).toHaveCount(0);
  expect(await page.evaluate(() => document.activeElement === document.body)).toBe(false);
});

test('the mobile viewport and theme-color metadata are declared', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('meta[name="viewport"]')).toHaveAttribute(
    'content',
    'width=device-width, initial-scale=1, viewport-fit=cover',
  );

  const colors = await page
    .locator('meta[name="theme-color"]')
    .evaluateAll((elements) => elements.map((element) => element.getAttribute('content')));
  expect(colors).toContain('#dceaf7');
  expect(colors).toContain('#080f29');
});

test('the skip link is the first tab stop, becomes visible and targets the stage', async ({ page }) => {
  await page.goto('/');
  await waitForShell(page);

  await page.keyboard.press('Tab');
  const skip = page.getByRole('link', { name: 'Skip to voice' });
  await expect(skip).toBeFocused();
  await expect(skip).toHaveAttribute('href', '#voice-stage');
  await expect(skip).toBeInViewport();
});

test.describe('coarse pointer', () => {
  test.use({ hasTouch: true, viewport: { width: 900, height: 720 } });

  test('drawer tabs, theme buttons, rows, menu toggle and search clear meet 44px', async ({ page }) => {
    await mockHistory(page);
    await gotoHistory(page);
    expect(await page.evaluate(() => matchMedia('(pointer: coarse)').matches)).toBe(true);

    for (const mode of ['Light', 'Dark']) {
      const box = await page.getByRole('button', { name: `Theme: ${mode}`, exact: true }).boundingBox();
      expect(box, `${mode} theme button`).not.toBeNull();
      expect(box!.width).toBeGreaterThanOrEqual(44);
      expect(box!.height).toBeGreaterThanOrEqual(44);
    }

    await page.getByRole('button', { name: 'Show conversations' }).click();
    const drawer = page.locator('#shell-left');
    await expect(drawer).toBeVisible();

    for (const tab of await drawer.getByRole('tab').all()) {
      const box = await tab.boundingBox();
      expect(box, 'drawer tab').not.toBeNull();
      expect(box!.height).toBeGreaterThanOrEqual(44);
    }

    const rowBox = await drawer.getByRole('button', { name: 'Trip to Paris', exact: true }).boundingBox();
    expect(rowBox, 'conversation row').not.toBeNull();
    expect(rowBox!.height).toBeGreaterThanOrEqual(44);

    const menuBox = await drawer.getByRole('button', { name: 'Conversation actions for Trip to Paris' }).boundingBox();
    expect(menuBox, 'row menu toggle').not.toBeNull();
    expect(menuBox!.width).toBeGreaterThanOrEqual(44);
    expect(menuBox!.height).toBeGreaterThanOrEqual(44);

    await page.getByRole('searchbox', { name: 'Search conversations' }).fill('paris');
    const clearBox = await drawer.getByRole('button', { name: 'Clear search' }).first().boundingBox();
    expect(clearBox, 'search clear').not.toBeNull();
    expect(clearBox!.width).toBeGreaterThanOrEqual(44);
    expect(clearBox!.height).toBeGreaterThanOrEqual(44);
  });
});
