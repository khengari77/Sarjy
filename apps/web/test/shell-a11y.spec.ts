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

test('Escape leaves a docked rail open with focus inside it', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto('/');
  await waitForShell(page);

  const rail = page.locator('#shell-left');
  await expect(rail).toBeVisible();
  const search = rail.getByRole('searchbox', { name: 'Search conversations' });
  await search.focus();
  await page.keyboard.press('Escape');

  await expect(rail).toBeVisible();
  await expect(rail).toHaveAttribute('data-open', 'true');
  await expect(search).toBeFocused();
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

  // Arrow/Home/End are consumed by the menu handler. With a single item focus
  // cannot move, so assert the handler really cancels the key's default action
  // (otherwise this passes even if focus management were removed).
  for (const key of ['ArrowDown', 'ArrowUp', 'Home', 'End']) {
    const prevented = await page.evaluate((pressed) => {
      const event = new KeyboardEvent('keydown', { key: pressed, bubbles: true, cancelable: true });
      document.activeElement?.dispatchEvent(event);
      return event.defaultPrevented;
    }, key);
    expect(prevented, `${key} must be consumed inside the menu`).toBe(true);
    await expect(deleteItem).toBeFocused();
    expect(await menu.evaluate((el) => el.contains(document.activeElement))).toBe(true);
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

test('the studio has exactly one page title', async ({ page }) => {
  await page.goto('/');
  await waitForShell(page);
  expect(await page.title()).toBe('Sarjy — Voice Context Studio');
  expect(await page.locator('title').count()).toBe(1);
});

test('the reviewer page has exactly one title and no skip link', async ({ page }) => {
  await page.goto('/review');
  expect(await page.title()).toBe('Reviewer access');
  expect(await page.locator('title').count()).toBe(1);
  await expect(page.getByRole('link', { name: 'Skip to voice' })).toHaveCount(0);
});

test('the theme-color meta follows the resolved theme, not the OS preference', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.goto('/');
  await waitForShell(page);
  const meta = page.locator('meta[name="theme-color"]');
  // Hydration collapses the media variants into one meta the session owns.
  await expect(meta).toHaveCount(1);
  await expect(meta).toHaveAttribute('content', '#080f29');

  await page.getByRole('button', { name: 'Theme: Light', exact: true }).click();
  await expect(meta).toHaveAttribute('content', '#dceaf7');

  await page.getByRole('button', { name: 'Theme: Dark', exact: true }).click();
  await expect(meta).toHaveAttribute('content', '#080f29');
});

test('the skip link is the first tab stop and moves focus to the stage', async ({ page }) => {
  await page.goto('/');
  await waitForShell(page);

  await page.keyboard.press('Tab');
  const skip = page.getByRole('link', { name: 'Skip to voice' });
  await expect(skip).toBeFocused();
  await expect(skip).toHaveAttribute('href', '#voice-stage');
  await expect(skip).toBeInViewport();

  await page.keyboard.press('Enter');
  await expect.poll(() => page.evaluate(() => document.activeElement?.id)).toBe('voice-stage');
});

test.describe('coarse pointer', () => {
  test.use({ hasTouch: true, viewport: { width: 900, height: 720 } });

  test('drawer tabs, theme buttons, header toggles, rows, menu toggle and search clear meet 44px', async ({ page }) => {
    await mockHistory(page);
    await gotoHistory(page);
    expect(await page.evaluate(() => matchMedia('(pointer: coarse)').matches)).toBe(true);

    for (const mode of ['Light', 'Dark']) {
      const box = await page.getByRole('button', { name: `Theme: ${mode}`, exact: true }).boundingBox();
      expect(box, `${mode} theme button`).not.toBeNull();
      expect(box!.width).toBeGreaterThanOrEqual(44);
      expect(box!.height).toBeGreaterThanOrEqual(44);
    }

    for (const label of ['Show conversations', 'Show memory and settings']) {
      const box = await page.getByRole('button', { name: label }).boundingBox();
      expect(box, `${label} header toggle`).not.toBeNull();
      expect(box!.width).toBeGreaterThanOrEqual(44);
      expect(box!.height).toBeGreaterThanOrEqual(44);
    }

    await page.getByRole('button', { name: 'Show memory and settings' }).click();
    const right = page.locator('#shell-right');
    await expect(right).toBeVisible();
    const tabs = right.getByRole('tab');
    await expect(tabs).toHaveCount(3);
    for (const tab of await tabs.all()) {
      const box = await tab.boundingBox();
      expect(box, 'drawer tab').not.toBeNull();
      expect(box!.height).toBeGreaterThanOrEqual(44);
    }

    // The header is inert while an overlay drawer is open.
    await right.getByRole('button', { name: 'Close' }).click();
    await expect(right).toBeHidden();
    await page.getByRole('button', { name: 'Show conversations' }).click();
    const drawer = page.locator('#shell-left');
    await expect(drawer).toBeVisible();

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
