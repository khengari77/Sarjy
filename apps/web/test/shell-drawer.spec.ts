import { expect, test, type Page } from '@playwright/test';

// Regression cover for the shell's drawer accessibility contract:
//  1. an open overlay drawer holds focus and the rest of the shell is inert,
//  2. every trigger's aria-controls resolves to its own drawer region,
//  3. resizing across the dock breakpoint never drops focus to <body>.
// The dock breakpoint is 1200px (shell.css); below it both panels are modal
// drawers, at or above it they are docked complementary regions.

const LEFT_TRIGGER = 'Show conversations';
const RIGHT_TRIGGER = 'Show memory and settings';

function leftTrigger(page: Page) {
  return page.getByRole('button', { name: LEFT_TRIGGER });
}

function rightTrigger(page: Page) {
  return page.getByRole('button', { name: RIGHT_TRIGGER });
}

/** SSR renders the orb statically; wait for the hydrated renderer so header
 *  controls have their handlers before we click them. */
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
    // This shared machine drops in-flight module requests on network changes
    // (net::ERR_NETWORK_CHANGED). That leaves SSR markup unhydrated or shows
    // SvelteKit's 500 page; a reload re-runs hydration.
    await page.reload({ waitUntil: 'domcontentloaded' });
  }
  throw new Error('shell never hydrated');
}

function activeLabel(page: Page): Promise<string | null> {
  return page.evaluate(
    () => document.activeElement?.getAttribute('aria-label') ?? document.activeElement?.tagName ?? null,
  );
}

function insideDrawer(page: Page, id: string): Promise<boolean> {
  return page.locator(`#${id}`).evaluate((el) => el.contains(document.activeElement));
}

/** The modal scope is the drawer plus the always-reachable control strip. */
async function insideModalScope(page: Page, id: string): Promise<boolean> {
  if (await insideDrawer(page, id)) return true;
  return page.locator('.stage-controls').evaluate((el) => el.contains(document.activeElement));
}

function isInert(page: Page, selector: string): Promise<boolean> {
  return page.locator(selector).evaluate((el) => (el as HTMLElement).inert);
}

test.beforeEach(async ({ page }) => {
  // These tests walk two viewports and the modal scope; give them room.
  test.setTimeout(90_000);
  page.setDefaultTimeout(20_000);
  await page.addInitScript(() => localStorage.setItem('sarjy-language', 'en'));
});

test('opening a drawer moves focus inside, makes the shell inert and Escape restores focus', async ({ page }) => {
  for (const viewport of [
    { width: 390, height: 844 },
    { width: 900, height: 720 },
  ]) {
    await page.setViewportSize(viewport);
    await page.goto('/');
    await waitForShell(page);

    // --- right drawer -------------------------------------------------------
    const right = rightTrigger(page);
    await right.click();
    const drawer = page.locator('#shell-right');
    await expect(drawer).toBeVisible();
    await expect(drawer).toHaveAttribute('role', 'dialog');
    await expect(drawer).toHaveAttribute('aria-modal', 'true');
    await expect(right).toHaveAttribute('aria-expanded', 'true');
    // Pointer-only dismissal, never a Tab stop.
    await expect(page.locator('.scrim')).toHaveAttribute('tabindex', '-1');
    // No stage dialog is open: the modal region holds nothing, so it is not a
    // stray focus stop.
    await expect(page.locator('.stage-dialog [role="dialog"]')).toHaveCount(0);

    // Focus landed on the first meaningful control inside the drawer, not on
    // the drawer container itself and not on <body>.
    await expect(drawer.getByRole('tab', { name: 'Voice' })).toBeFocused();
    expect(await page.evaluate(() => document.activeElement === document.body)).toBe(false);
    expect(await drawer.evaluate((el) => el === document.activeElement)).toBe(false);

    // The background is inert for real: property + attribute, not a CSS class.
    expect(await isInert(page, '.topbar')).toBe(true);
    expect(await isInert(page, '.stage-content')).toBe(true);
    await expect(page.locator('.topbar')).toHaveAttribute('inert', '');
    await expect(page.locator('.stage-content')).toHaveAttribute('inert', '');
    // #37: the call-control/composer strip is deliberately outside the inert set.
    expect(await isInert(page, '.stage-controls')).toBe(false);

    // Tab order is confined to the modal scope: the drawer plus the control
    // strip. Without the trap it would reach the (inert) header and stage body,
    // or browser chrome.
    for (let index = 0; index < 16; index += 1) {
      expect(await insideModalScope(page, 'shell-right'), `tab #${index + 1} escaped the modal scope`).toBe(true);
    }

    // Programmatic focus on an inert background control is refused too.
    await page.locator('.theme-button').evaluate((el) => (el as HTMLElement).focus());
    expect(await insideDrawer(page, 'shell-right')).toBe(true);

    await page.keyboard.press('Escape');
    await expect(right).toHaveAttribute('aria-expanded', 'false');
    await expect(drawer).not.toBeVisible();
    expect(await isInert(page, '.topbar')).toBe(false);
    expect(await isInert(page, '.stage-content')).toBe(false);
    await expect(right).toBeFocused();

    // Complement assertion: the background is interactive again, not merely
    // "not hidden". A control that was inert a moment ago now takes focus.
    await page.locator('.theme-button').evaluate((el) => (el as HTMLElement).focus());
    expect(await page.evaluate(() => document.activeElement?.className)).toContain('theme-button');
    await page.locator('#voice-stage').getByRole('button').first().focus();
    expect(await page.evaluate(() => document.activeElement?.closest('#voice-stage') !== null)).toBe(true);
    await right.focus();

    // --- left drawer --------------------------------------------------------
    const left = leftTrigger(page);
    await left.click();
    const leftDrawer = page.locator('#shell-left');
    await expect(leftDrawer).toBeVisible();
    await expect(leftDrawer).toHaveAttribute('role', 'dialog');
    await expect(leftDrawer.getByRole('button', { name: /New conversation/ })).toBeFocused();
    expect(await isInert(page, '.topbar')).toBe(true);
    expect(await isInert(page, '.stage-content')).toBe(true);

    await page.keyboard.press('Escape');
    await expect(left).toHaveAttribute('aria-expanded', 'false');
    await expect(leftDrawer).not.toBeVisible();
    await expect(left).toBeFocused();
    expect(await isInert(page, '.topbar')).toBe(false);
  }
});

test('every drawer trigger exposes aria-controls that resolves to its own live region', async ({ page }) => {
  for (const viewport of [
    { width: 390, height: 844 },
    { width: 900, height: 720 },
    { width: 1280, height: 720 },
  ]) {
    await page.setViewportSize(viewport);
    await page.goto('/');
    await waitForShell(page);

    for (const [accessibleName, id] of [
      [LEFT_TRIGGER, 'shell-left'],
      [RIGHT_TRIGGER, 'shell-right'],
    ] as const) {
      const trigger = page.getByRole('button', { name: accessibleName });
      await expect(trigger).toHaveAttribute('aria-controls', id);

      const region = await page.evaluate((regionId) => {
        const element = document.getElementById(regionId);
        if (!element) return null;
        return {
          count: document.querySelectorAll(`#${regionId}`).length,
          tag: element.tagName,
          role: element.getAttribute('role'),
          label: element.getAttribute('aria-label'),
        };
      }, id);
      expect(region, `${id} must exist for ${accessibleName}`).not.toBeNull();
      expect(region!.count).toBe(1);
      expect(region!.tag).toBe('ASIDE');
      expect(region!.label).toBeTruthy();

      // aria-expanded tracks the live data-open state in both layouts.
      const open = await page.locator(`#${id}`).getAttribute('data-open');
      await expect(trigger).toHaveAttribute('aria-expanded', open === 'true' ? 'true' : 'false');
    }

    // No dangling aria-controls anywhere in the shell.
    const dangling = await page.evaluate(() =>
      Array.from(document.querySelectorAll('[aria-controls]'))
        .map((element) => element.getAttribute('aria-controls') ?? '')
        .filter((id) => id !== '' && !document.getElementById(id)),
    );
    expect(dangling).toEqual([]);

    // Opening a drawer gives the region an explicit dialog role + label; a
    // closed one falls back to its native complementary aside semantics.
    await rightTrigger(page).click();
    const region = page.locator('#shell-right');
    await expect(region).toHaveAttribute('aria-label', 'Memory and settings');
    if (viewport.width < 1200) {
      await expect(region).toHaveAttribute('role', 'dialog');
      await expect(region).toHaveAttribute('aria-modal', 'true');
    } else {
      await expect(region).not.toHaveAttribute('role', 'dialog');
    }
  }
});

test('resizing across the dock breakpoint keeps focus inside the drawer or restores it to the trigger', async ({
  page,
}) => {
  // Mobile overlay drawer → desktop dock: the focused element must stay
  // focused (the drawer keeps rendering), not fall to <body>.
  await page.setViewportSize({ width: 900, height: 720 });
  await page.goto('/');
  await waitForShell(page);

  const right = rightTrigger(page);
  await right.click();
  const drawer = page.locator('#shell-right');
  await expect(drawer).toHaveAttribute('role', 'dialog');
  await drawer.getByRole('tab', { name: 'Appearance' }).click();
  await expect(drawer.getByRole('tab', { name: 'Appearance' })).toBeFocused();

  await page.setViewportSize({ width: 1440, height: 900 });
  await expect(right).toHaveAttribute('aria-expanded', 'true');
  await expect(drawer).toBeVisible();
  await expect(drawer).not.toHaveAttribute('role', 'dialog');
  await expect(drawer.getByRole('tab', { name: 'Appearance' })).toBeFocused();
  expect(await page.evaluate(() => document.activeElement === document.body)).toBe(false);
  expect(await isInert(page, '.topbar')).toBe(false);
  expect(await isInert(page, '.stage-content')).toBe(false);

  // Desktop dock → mobile overlay: the dock collapses, so focus is
  // deliberately restored to the trigger that owns the panel - never <body>.
  await page.setViewportSize({ width: 700, height: 900 });
  await expect(right).toHaveAttribute('aria-expanded', 'false');
  await expect(right).toBeFocused();
  await expect(drawer).not.toBeVisible();
  expect(await page.evaluate(() => document.activeElement === document.body)).toBe(false);

  // Same contract for the left dock, which starts open on desktop.
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');
  await waitForShell(page);
  const left = leftTrigger(page);
  await expect(left).toHaveAttribute('aria-expanded', 'true');
  // The boot reload disables the new-chat button for a moment; retry until
  // focus actually lands inside the dock before shrinking the viewport.
  await expect
    .poll(async () => {
      await page
        .locator('#shell-left')
        .getByRole('button', { name: /New conversation/ })
        .focus();
      return insideDrawer(page, 'shell-left');
    })
    .toBe(true);

  await page.setViewportSize({ width: 800, height: 900 });
  await expect(left).toHaveAttribute('aria-expanded', 'false');
  await expect(left).toBeFocused();
  await expect(page.locator('#shell-left')).not.toBeVisible();
  expect(await activeLabel(page)).toBe(LEFT_TRIGGER);
  expect(await page.evaluate(() => document.activeElement === document.body)).toBe(false);
});

test('Escape in the stage does not close a docked panel behind a stage dialog', async ({ page }) => {
  // Docked panels only consume Escape when focus is in the panel or on its
  // trigger; nested stage dialogs (transcript, intent confirm) keep their own
  // Escape handling and their own focus restoration.
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto('/');
  await waitForShell(page);

  const left = leftTrigger(page);
  await expect(left).toHaveAttribute('aria-expanded', 'true');
  const composer = page.locator('#voice-stage textarea').first();
  // The boot reload leaves controls disabled for a moment; retry until the
  // composer itself holds focus so the Escape assertion is meaningful.
  await expect
    .poll(async () => {
      await composer.focus();
      return composer.evaluate((el) => el === document.activeElement);
    })
    .toBe(true);

  await page.keyboard.press('Escape');
  await expect(left).toHaveAttribute('aria-expanded', 'true');
  await expect(page.locator('#shell-left')).toBeVisible();
  await expect(composer).toBeFocused();

  // ... but Escape from the trigger itself still closes the dock.
  await left.focus();
  await page.keyboard.press('Escape');
  await expect(left).toHaveAttribute('aria-expanded', 'false');
  await expect(left).toBeFocused();
});
