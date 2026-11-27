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

      // The layer must not inherit the document's LTR: it carries the UI
      // direction explicitly. (Removing the attribute breaks this assertion.)
      expect(await page.locator('.change-toast-layer').getAttribute('dir')).toMatch(/^(ltr|rtl)$/);
      await expectToastInsideViewport(page);
      await expectToastClearsDock(page);
    });
  }
});

test('the toast follows the main column when the shell flips to RTL', async ({ page }) => {
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
