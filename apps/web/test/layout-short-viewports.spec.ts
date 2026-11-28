import { expect, test } from '@playwright/test';
import {
  boxOf,
  boot,
  expectToastClearsDock,
  expectToastInsideViewport,
  hitTarget,
  installRuntime,
  intersects,
  showChangeToast,
  startLive,
} from './support/layoutHarness';

// UX-6A round 2: short viewports, RTL placement and toast clamping.

test.describe('toast horizontal bounds in LTR and RTL', () => {
  test.describe.configure({ timeout: 120_000 });

  for (const config of [
    { width: 390, rtl: false },
    { width: 390, rtl: true },
    { width: 320, rtl: true },
  ]) {
    test(`${config.width}px ${config.rtl ? 'RTL' : 'LTR'}`, async ({ page }) => {
      await page.setViewportSize({ width: config.width, height: 844 });
      await installRuntime(page);
      await boot(page);
      if (config.rtl) {
        // Production only sets `dir` on the shell; the toast is mounted
        // outside it and must still be centred over the main column.
        await page.locator('.shell').evaluate((el) => el.setAttribute('dir', 'rtl'));
        expect(await page.evaluate(() => document.documentElement.dir)).not.toBe('rtl');
      }
      await startLive(page);
      await showChangeToast(page);

      await expectToastInsideViewport(page);
      await expectToastClearsDock(page);
    });
  }
});

test('the toast layer follows the shell dir', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await installRuntime(page);
  await boot(page);
  await startLive(page);
  await showChangeToast(page);

  const centres = () =>
    page.evaluate(() => {
      const toast = document.querySelector('.change-toast')!.getBoundingClientRect();
      const column = document.querySelector('.main-column')!.getBoundingClientRect();
      return {
        toast: toast.x + toast.width / 2,
        column: column.x + column.width / 2,
      };
    });

  const before = await centres();
  expect(Math.abs(before.toast - before.column)).toBeLessThanOrEqual(1);

  await page.locator('.shell').evaluate((el) => el.setAttribute('dir', 'rtl'));
  // The release UI is fixed to English, so the test cannot flip the AppShell
  // `dir` prop; a resize schedules the same measurement pass the prop change
  // triggers in production. Without it the toast keeps the stale centre.
  await page.evaluate(() => window.dispatchEvent(new Event('resize')));
  await expect
    .poll(async () => {
      const now = await centres();
      return Math.abs(now.toast - now.column);
    })
    .toBeLessThanOrEqual(1);

  const after = await centres();
  expect(Math.abs(after.column - before.column), 'the rail swap moves the main column').toBeGreaterThan(50);
});

test.describe('short viewports keep the stage reachable', () => {
  test.describe.configure({ timeout: 120_000 });

  for (const config of [
    { label: '320x256 live', viewport: { width: 320, height: 256 }, live: true },
    { label: '320x200 idle', viewport: { width: 320, height: 200 }, live: false },
    { label: '640x400 live', viewport: { width: 640, height: 400 }, live: true },
  ] as const) {
    test(config.label, async ({ page }) => {
      await page.setViewportSize(config.viewport);
      await installRuntime(page);
      await boot(page);
      if (config.live) await startLive(page);

      const stage = page.locator('.voice-stage');
      const stageHeight = await stage.evaluate((el) => el.clientHeight);
      // Short layouts reserve a full orb + status + caption block (240px), so
      // the stage cannot collapse under the dock.
      expect(stageHeight, 'stage keeps a usable height').toBeGreaterThanOrEqual(240);

      // Start from the top of both scroll containers: the orb must be hit-testable there.
      await page.evaluate(() => {
        (document.querySelector('.main-column') as HTMLElement).scrollTop = 0;
        (document.querySelector('.voice-stage') as HTMLElement).scrollTop = 0;
      });
      const orb = await hitTarget(page.locator('.orb-button'));
      expect(orb, `orb hit target: ${JSON.stringify(orb)}`).toMatchObject({ insideViewport: true, hit: true });

      const status = page.locator('.status-line').first();
      await status.scrollIntoViewIfNeeded();
      const statusHit = await hitTarget(status);
      expect(statusHit, `status hit target: ${JSON.stringify(statusHit)}`).toMatchObject({
        insideViewport: true,
        hit: true,
      });

      if (config.live) {
        const caption = page.locator('.caption-bubble');
        await expect(caption).toBeVisible();
        await caption.scrollIntoViewIfNeeded();
        const captionHit = await hitTarget(caption);
        expect(captionHit, `caption hit target: ${JSON.stringify(captionHit)}`).toMatchObject({
          insideViewport: true,
          hit: true,
        });
      }
    });
  }
});

test.describe('the toast re-measures when the dock scrolls into view', () => {
  test.describe.configure({ timeout: 120_000 });

  for (const viewport of [
    { width: 320, height: 256 },
    { width: 640, height: 400 },
  ]) {
    test(`${viewport.width}x${viewport.height}`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await installRuntime(page);
      await boot(page);
      await startLive(page);
      await showChangeToast(page);

      // Scroll just enough to show ~112px of the dock. At 320px this leaves
      // the minimum toast band; at 640px both scroll to the end.
      await page.evaluate(() => {
        const main = document.querySelector<HTMLElement>('.main-column')!;
        const dock = document.querySelector<HTMLElement>('.stage-controls')!;
        const target = dock.getBoundingClientRect().top - (innerHeight - 112);
        const max = main.scrollHeight - main.clientHeight;
        main.scrollTop = Math.max(0, Math.min(max, main.scrollTop + target));
        main.dispatchEvent(new Event('scroll'));
      });

      await expect
        .poll(async () => {
          const toast = await boxOf(page.locator('.change-toast'));
          const dock = await boxOf(page.locator('.stage-controls'));
          return dock.y - (toast.y + toast.height);
        })
        .toBeGreaterThanOrEqual(11.5);

      await expectToastClearsDock(page);
    });
  }
});

test.describe('the toast never covers a fully revealed dock', () => {
  test.describe.configure({ timeout: 120_000 });

  for (const viewport of [
    { width: 320, height: 256 },
    { width: 320, height: 200 },
  ]) {
    test(`${viewport.width}x${viewport.height} at full scroll`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await installRuntime(page);
      await boot(page);
      await startLive(page);
      await showChangeToast(page);

      await page.evaluate(() => {
        const main = document.querySelector<HTMLElement>('.main-column')!;
        main.scrollTop = main.scrollHeight - main.clientHeight;
        main.dispatchEvent(new Event('scroll'));
      });

      // After the UX-2 merge the dock is 206px tall at 320px, so a full scroll
      // leaves only ~30px above it (topbar scrolled to y=-190, toastTop 8,
      // dockTop 50): too little for the 66px toast. Hidden is correct; the
      // always-present live region keeps the change announced.
      const hidden = await page.evaluate(() => document.documentElement.dataset.toastReady !== 'true');
      if (hidden) {
        expect(await page.locator('.change-toast').isVisible()).toBe(false);
        await expect(page.locator('.change-live')).toHaveText('Saved to memory');
        expect(await page.locator('.change-live').evaluate((el) => getComputedStyle(el).display)).not.toBe('none');
      } else {
        await expectToastClearsDock(page);
        // The visual toast announces; the fallback live region is suppressed.
        expect(await page.locator('.change-live').evaluate((el) => getComputedStyle(el).display)).toBe('none');
      }
    });
  }
});

test('the toast sits below an open drawer', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installRuntime(page);
  await boot(page);
  await showChangeToast(page);

  await page.getByRole('button', { name: 'Show memory and settings' }).click();
  await expect(page.locator('#shell-right')).toHaveAttribute('data-open', 'true');
  // data-open flips at once but the drawer slides in: hit-testing before the
  // transition ends sees the toast still on top (a flake on slow CI runners).
  // Reading the computed transform forces the style flush that starts the slide, so an empty
  // getAnimations() cannot be a stale pre-transition read; the settled drawer sits at the identity.
  await page.waitForFunction(() => {
    const drawer = document.querySelector('#shell-right')!;
    const transform = getComputedStyle(drawer).transform;
    return drawer.getAnimations().length === 0 && (transform === 'none' || transform === 'matrix(1, 0, 0, 1, 0, 0)');
  });

  const zIndexes = await page.evaluate(() => ({
    toast: Number(getComputedStyle(document.querySelector('.change-toast-layer')!).zIndex),
    drawer: Number(getComputedStyle(document.querySelector('#shell-right')!).zIndex),
    controls: Number(getComputedStyle(document.querySelector('.stage-controls')!).zIndex),
  }));
  expect(zIndexes.toast, 'toast above the controls strip').toBeGreaterThan(zIndexes.controls);
  expect(zIndexes.toast, 'toast below the modal drawer').toBeLessThan(zIndexes.drawer);

  const toast = await boxOf(page.locator('.change-toast'));
  const drawer = await boxOf(page.locator('#shell-right'));
  if (intersects(toast, drawer)) {
    const toastOnTop = await page.evaluate(
      ({ x, y }) => {
        const node = document.elementFromPoint(x, y);
        return node !== null && node.closest('.change-toast') !== null;
      },
      { x: toast.x + toast.width / 2, y: toast.y + toast.height / 2 },
    );
    expect(toastOnTop, 'an open drawer wins the hit test over the toast').toBe(false);
  }
});

test.describe('the toast stays inside short viewports', () => {
  test.describe.configure({ timeout: 120_000 });

  for (const viewport of [
    { width: 320, height: 200 },
    { width: 320, height: 256 },
  ]) {
    test(`${viewport.width}x${viewport.height}`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await installRuntime(page);
      await boot(page);
      await startLive(page);
      await showChangeToast(page);

      const toast = await expectToastInsideViewport(page);
      const buttons = page.locator('.topbar button');
      for (let index = 0; index < (await buttons.count()); index += 1) {
        const button = await boxOf(buttons.nth(index));
        expect(intersects(toast, button), `toast must not cover topbar button ${index}`).toBe(false);
      }
    });
  }
});

test('the dismiss control gets a 44px target on touch pointers', async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true });
  const page = await context.newPage();
  try {
    await installRuntime(page);
    await boot(page);
    await startLive(page);
    await showChangeToast(page);

    const dismiss = await boxOf(page.locator('.change-dismiss'));
    expect(dismiss.width).toBeGreaterThanOrEqual(44);
    expect(dismiss.height).toBeGreaterThanOrEqual(44);
  } finally {
    await context.close();
  }
});
