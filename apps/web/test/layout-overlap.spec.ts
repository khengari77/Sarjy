import { expect, test } from '@playwright/test';
import {
  boot,
  boxOf,
  expectHit,
  installRuntime,
  intersects,
  showChangeToast,
  startLive,
} from './support/layoutHarness';

// UX-6A: overlapping/clipped chrome found by the live screenshot audit.
// The toast must clear the whole bottom dock, the stage must scroll instead of
// clipping, the composer must grow with a draft, and the notice must not sit
// over the controls.

const VIEWPORTS = [
  { width: 360, height: 640 },
  { width: 390, height: 844 },
  { width: 768, height: 1024 },
  { width: 1024, height: 768 },
  { width: 1280, height: 800 },
];

test.describe('live change toast clears the bottom dock', () => {
  for (const viewport of VIEWPORTS) {
    test(`${viewport.width}x${viewport.height}`, async ({ browser }) => {
      const context = await browser.newContext({ viewport });
      const page = await context.newPage();
      try {
        await installRuntime(page);
        await boot(page);
        await startLive(page);
        await showChangeToast(page);
        await page.locator('.composer textarea').fill('Hello');

        const toast = await boxOf(page.locator('.change-toast'));
        const dock = await boxOf(page.locator('.stage-controls'));
        const callControls = await boxOf(page.locator('.controls'));
        const composer = await boxOf(page.locator('.composer'));

        // (a) The toast never steals a control's hit target.
        await expectHit(page.getByRole('button', { name: 'End call', exact: true }), 'End');
        await expectHit(page.getByRole('button', { name: 'Mute', exact: true }), 'Mute');
        await expectHit(page.getByRole('button', { name: 'Stop', exact: true }), 'Stop');
        await expectHit(page.getByRole('button', { name: 'Send', exact: true }), 'Send');

        // (b) Toast above the whole dock with at least a 12px gap.
        expect(intersects(toast, dock), 'toast must not intersect the dock').toBe(false);
        expect(intersects(toast, callControls), 'toast must not intersect the call controls').toBe(false);
        expect(intersects(toast, composer), 'toast must not intersect the composer').toBe(false);
        expect(dock.y - (toast.y + toast.height), 'gap above the dock').toBeGreaterThanOrEqual(11.5);

        // Horizontal anchor follows the main column, not the window centre.
        const column = await boxOf(page.locator('.main-column'));
        expect(Math.abs(toast.x + toast.width / 2 - (column.x + column.width / 2))).toBeLessThanOrEqual(1);
      } finally {
        await context.close();
      }
    });
  }
});

test('640x400 scrolls the stage and keeps the orb and status reachable', async ({ page }) => {
  await page.setViewportSize({ width: 640, height: 400 });
  await installRuntime(page);
  await boot(page);
  await startLive(page);

  const stage = page.locator('.voice-stage');
  expect(await stage.evaluate((el) => getComputedStyle(el).overflowY)).toMatch(/auto|scroll/);

  for (const [label, locator] of [
    ['orb', page.locator('.orb-button')],
    ['status', page.locator('.status-line').first()],
  ] as const) {
    await locator.scrollIntoViewIfNeeded();
    const box = await boxOf(locator);
    const stageBox = await boxOf(stage);
    expect(box.y, `${label} top inside the stage`).toBeGreaterThanOrEqual(stageBox.y - 0.5);
    expect(box.y + box.height, `${label} bottom inside the stage`).toBeLessThanOrEqual(
      stageBox.y + stageBox.height + 0.5,
    );
    expect(box.y, `${label} top inside the viewport`).toBeGreaterThanOrEqual(0);
    expect(box.y + box.height, `${label} bottom inside the viewport`).toBeLessThanOrEqual(400);
  }
});

test('the mic-denied notice never overlaps the status, controls or composer', async ({ browser }) => {
  for (const viewport of VIEWPORTS) {
    const context = await browser.newContext({ viewport });
    const page = await context.newPage();
    try {
      await installRuntime(page, true);
      await boot(page);
      await page.locator('.orb-button').click();

      const notice = page.locator('[data-code^="MICROPHONE_"]');
      await expect(notice, `${viewport.width}x${viewport.height} notice`).toBeVisible();
      const boxes = {
        notice: await boxOf(notice),
        status: await boxOf(page.locator('.status-line').first()),
        controls: await boxOf(page.locator('.controls')),
        composer: await boxOf(page.locator('.composer')),
      };
      expect(intersects(boxes.notice, boxes.status), 'notice must not overlap the status line').toBe(false);
      expect(intersects(boxes.notice, boxes.controls), 'notice must not overlap the call controls').toBe(false);
      expect(intersects(boxes.notice, boxes.composer), 'notice must not overlap the composer').toBe(false);
      expect(intersects(boxes.status, boxes.controls), 'status must not overlap the call controls').toBe(false);
      expect(intersects(boxes.status, boxes.composer), 'status must not overlap the composer').toBe(false);
    } finally {
      await context.close();
    }
  }
});

test('a three-line composer draft is fully visible and grows the pill', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installRuntime(page);
  await boot(page);

  const field = page.locator('.composer textarea');
  await field.fill('First line\nSecond line\nThird line');
  await expect
    .poll(async () => field.evaluate((el) => el.scrollHeight <= el.clientHeight + 1 || el.clientHeight >= 3 * 24))
    .toBe(true);

  const fieldBox = await boxOf(field);
  const composerBox = await boxOf(page.locator('.composer'));
  expect(fieldBox.height).toBeGreaterThanOrEqual(3 * 24 - 1);
  expect(composerBox.height).toBeGreaterThanOrEqual(fieldBox.height);
});

test('a long caption caps at three lines and scrolls without truncation', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const text =
    'This is a deliberately long assistant reply that must remain complete in the caption bubble even when it ' +
    'runs well past three rendered lines on a narrow phone. It should scroll inside the bubble instead of being ' +
    'silently cut, and the transcript button still carries the full text for screen readers and the drawer.';
  await installRuntime(page);
  await boot(page, [{ id: 't1', role: 'assistant', text, status: 'completed' }]);

  const bubble = page.locator('.caption-bubble');
  await expect(bubble).toContainText('the drawer.');
  const values = await bubble.evaluate((el) => ({
    height: el.getBoundingClientRect().height,
    clientHeight: el.clientHeight,
    scrollHeight: el.scrollHeight,
    overflowY: getComputedStyle(el).overflowY,
  }));
  expect(values.scrollHeight).toBeGreaterThan(values.clientHeight);
  expect(values.height).toBeLessThanOrEqual(92.5);
  expect(values.overflowY).toBe('auto');
});

test('the Undo control keeps the accent colour and a 44px target', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installRuntime(page);
  await boot(page);
  await startLive(page);
  await showChangeToast(page);

  const undo = page.locator('.change-undo');
  await expect(undo).toBeVisible();
  const values = await undo.evaluate((el) => {
    const probe = document.createElement('span');
    probe.style.color = 'var(--accent)';
    document.body.appendChild(probe);
    const accent = getComputedStyle(probe).color;
    probe.remove();
    return { color: getComputedStyle(el).color, accent, height: el.getBoundingClientRect().height };
  });
  expect(values.color).toBe(values.accent);
  expect(values.height).toBeGreaterThanOrEqual(44);

  // Reach Undo through the keyboard so :focus-visible (the focus ring) applies.
  let focused = false;
  for (let press = 0; press < 25; press += 1) {
    await page.keyboard.press('Tab');
    if (await undo.evaluate((el) => el === document.activeElement)) {
      focused = true;
      break;
    }
  }
  expect(focused, 'Undo must be reachable in the tab order').toBe(true);
  expect(await undo.evaluate((el) => getComputedStyle(el).outlineStyle)).not.toBe('none');
});
