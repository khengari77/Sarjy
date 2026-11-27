import { expect, test, type Page } from '@playwright/test';
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
  { width: 1440, height: 900 },
];

// The toast sits 12px above the measured dock; the bounds keep it clear of the
// controls without floating far up the stage.
const TOAST_GAP_MIN = 11.5;
const TOAST_GAP_MAX = 24;
// Require a real gap, not merely non-overlapping edges.
const CLEARANCE = 4;

async function expectToastClearsDock(page: Page) {
  const toast = await boxOf(page.locator('.change-toast'));
  const dock = await boxOf(page.locator('.stage-controls'));
  const callControls = await boxOf(page.locator('.controls'));
  const composer = await boxOf(page.locator('.composer'));

  expect(intersects(toast, dock, CLEARANCE), 'toast must clear the dock by a margin').toBe(false);
  expect(intersects(toast, callControls, CLEARANCE), 'toast must clear the call controls by a margin').toBe(false);
  expect(intersects(toast, composer, CLEARANCE), 'toast must clear the composer by a margin').toBe(false);

  const gap = dock.y - (toast.y + toast.height);
  expect(gap, 'gap above the dock').toBeGreaterThanOrEqual(TOAST_GAP_MIN);
  expect(gap, 'toast must stay close above the dock').toBeLessThanOrEqual(TOAST_GAP_MAX);

  // Horizontal anchor follows the main column, not the window centre.
  const column = await boxOf(page.locator('.main-column'));
  expect(Math.abs(toast.x + toast.width / 2 - (column.x + column.width / 2))).toBeLessThanOrEqual(1);
}

test.describe('live change toast clears the bottom dock', () => {
  // Six full boots (orb animation + live session) in one test.
  test.describe.configure({ timeout: 120_000 });

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

        // (a) The toast never steals a control's hit target.
        await expectHit(page.getByRole('button', { name: 'End call', exact: true }), 'End');
        await expectHit(page.getByRole('button', { name: 'Mute', exact: true }), 'Mute');
        await expectHit(page.getByRole('button', { name: 'Stop', exact: true }), 'Stop');
        await expectHit(page.getByRole('button', { name: 'Send', exact: true }), 'Send');

        // (b) Toast above the whole dock, within the expected band.
        await expectToastClearsDock(page);
      } finally {
        await context.close();
      }
    });
  }
});

test('the toast clears the dock in the dark theme', async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, colorScheme: 'dark' });
  const page = await context.newPage();
  try {
    await installRuntime(page, false, { theme: 'dark' });
    await boot(page);
    await startLive(page);
    await showChangeToast(page);

    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await expectToastClearsDock(page);
    await expectHit(page.getByRole('button', { name: 'End call', exact: true }), 'End');
  } finally {
    await context.close();
  }
});

test('the toast clears the dock in RTL', async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await context.newPage();
  try {
    await installRuntime(page);
    await boot(page);
    // The release UI is fixed to English; the layout still has to hold when
    // the shell is rendered right-to-left.
    await page.evaluate(() => {
      document.documentElement.dir = 'rtl';
      document.querySelector('.shell')?.setAttribute('dir', 'rtl');
    });
    await expect(page.locator('.shell')).toHaveAttribute('dir', 'rtl');
    await page.locator('.orb-button').click();
    await expect(page.locator('.controls button').first()).toBeVisible();
    await showChangeToast(page);

    await expectToastClearsDock(page);
    await expectHit(page.locator('.composer .send'), 'Send');
  } finally {
    await context.close();
  }
});

test('640x400 keeps the caption and status reachable without scrolling past the orb', async ({ page }) => {
  await page.setViewportSize({ width: 640, height: 400 });
  await installRuntime(page);
  await boot(page);
  await startLive(page);

  const stage = page.locator('.voice-stage');
  expect(await stage.evaluate((el) => getComputedStyle(el).overflowY)).toMatch(/auto|scroll/);
  const stageBox = await boxOf(stage);

  // `safe center` (with the plain-centre fallback) starts overflowing content
  // at the top; plain centring would clip the orb above an unreachable edge.
  await stage.evaluate((el) => {
    el.scrollTop = 0;
  });
  const orbAtTop = await boxOf(page.locator('.orb-button'));
  expect(orbAtTop.y, 'orb top is reachable at the stage top').toBeGreaterThanOrEqual(stageBox.y - 0.5);

  // Revealing the caption must not scroll the orb out of the stage.
  const caption = page.locator('.caption-bubble');
  await caption.scrollIntoViewIfNeeded();

  for (const [label, locator] of [
    ['caption', caption],
    ['status', page.locator('.status-line').first()],
  ] as const) {
    const box = await boxOf(locator);
    expect(box.y, `${label} top inside the stage`).toBeGreaterThanOrEqual(stageBox.y - 0.5);
    expect(box.y + box.height, `${label} bottom inside the stage`).toBeLessThanOrEqual(
      stageBox.y + stageBox.height + 0.5,
    );
    expect(box.y, `${label} top inside the viewport`).toBeGreaterThanOrEqual(0);
    expect(box.y + box.height, `${label} bottom inside the viewport`).toBeLessThanOrEqual(400);
  }

  const orbAfterScroll = await boxOf(page.locator('.orb-button'));
  expect(orbAfterScroll.y + orbAfterScroll.height, 'orb is still visible above the stage bottom').toBeGreaterThan(
    stageBox.y,
  );
  expect(orbAfterScroll.y, 'orb has not been scrolled entirely out of the stage').toBeLessThan(
    stageBox.y + stageBox.height,
  );
});

test('the mic-denied notice never overlaps the status, controls or composer', async ({ browser }) => {
  test.setTimeout(120_000);
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
      expect(intersects(boxes.notice, boxes.status, CLEARANCE), 'notice must clear the status line').toBe(false);
      expect(intersects(boxes.notice, boxes.controls, CLEARANCE), 'notice must clear the call controls').toBe(false);
      expect(intersects(boxes.notice, boxes.composer, CLEARANCE), 'notice must clear the composer').toBe(false);
      expect(intersects(boxes.status, boxes.controls, CLEARANCE), 'status must clear the call controls').toBe(false);
      expect(intersects(boxes.status, boxes.composer, CLEARANCE), 'status must clear the composer').toBe(false);
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

test('the composer re-fits a wrapped draft when the viewport narrows', async ({ page }) => {
  await page.setViewportSize({ width: 520, height: 700 });
  await installRuntime(page);
  await boot(page);

  const field = page.locator('.composer textarea');
  await field.fill('A single draft line that rewraps when the composer gets much narrower');
  const wideHeight = await field.evaluate((el) => el.getBoundingClientRect().height);

  await page.setViewportSize({ width: 300, height: 700 });
  await expect.poll(async () => field.evaluate((el) => el.getBoundingClientRect().height)).toBeGreaterThan(wideHeight);

  const fits = await field.evaluate((el) => el.scrollHeight <= el.clientHeight + 1 || el.clientHeight >= 96);
  expect(fits, 'rewrapped draft fits or caps at four lines').toBe(true);
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
