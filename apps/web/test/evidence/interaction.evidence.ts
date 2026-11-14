import { expect, test } from '@playwright/test';
import {
  annotatePending,
  capture,
  captureLocator,
  evidenceSha,
  hasThemeControl,
  mockApi,
  orbCanvasIdentity,
  selectTheme,
  waitForHydration,
  writeEvidenceJson,
  type EvidenceTheme,
} from './support/evidence';

const DESKTOP = { width: 1440, height: 900 };
const MOBILE = { width: 390, height: 844 };
const RIGHT_TRIGGER = 'Show memory and settings';

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('sarjy-language', 'en'));
});

test('theme toggle persists and never posts a voice session', async ({ page }, testInfo) => {
  await page.setViewportSize(DESKTOP);
  await page.emulateMedia({ colorScheme: 'dark' });
  const api = await mockApi(page);
  await page.goto('/');
  await waitForHydration(page);

  if (!(await hasThemeControl(page))) {
    const reason = 'Theme control is not on this base (pending #120).';
    annotatePending(testInfo, '#120 two-theme shell control');
    test.skip(true, reason);
    return;
  }

  const html = page.locator('html');
  const toggle = page.locator('.theme-button');
  await expect(html).toHaveAttribute('data-theme', 'dark');
  await expect(html).toHaveAttribute('data-theme-preference', 'system');

  await toggle.click();
  await expect(html).toHaveAttribute('data-theme', 'light');
  await expect(html).toHaveAttribute('data-theme-preference', 'light');
  expect(await page.evaluate(() => localStorage.getItem('sarjy-theme'))).toBe('light');

  await page.reload();
  await waitForHydration(page);
  await expect(html).toHaveAttribute('data-theme', 'light');
  await expect(html).toHaveAttribute('data-theme-preference', 'light');

  await toggle.click();
  await expect(html).toHaveAttribute('data-theme', 'dark');
  expect(await page.evaluate(() => localStorage.getItem('sarjy-theme'))).toBe('dark');

  await page.waitForTimeout(300);
  expect(api.sessionPosts, 'theme toggling must not request a voice session').toBe(0);
  expect(await orbCanvasIdentity(page), 'theme toggling must not recreate the orb canvas').toBe(true);

  const screenshot = await capture(page, 'theme-toggle-dark.png');
  await writeEvidenceJson('theme-toggle.json', {
    sha: evidenceSha(),
    sessionPosts: api.sessionPosts,
    persisted: await page.evaluate(() => localStorage.getItem('sarjy-theme')),
    screenshot,
  });
});

test('drawer opens and closes without reconnecting or posting a session', async ({ page }) => {
  await page.setViewportSize(MOBILE);
  const api = await mockApi(page);
  await page.goto('/');
  await waitForHydration(page);

  const trigger = page.getByRole('button', { name: RIGHT_TRIGGER });
  const panel = page.locator('aside[aria-label="Memory and settings"]');
  const closedScreenshot = await capture(page, 'drawer-mobile-closed.png');

  await trigger.click();
  await expect(trigger).toHaveAttribute('aria-expanded', 'true');
  await expect(panel).toHaveAttribute('data-open', 'true');
  const openScreenshot = await capture(page, 'drawer-mobile-open.png');

  const scrim = page.getByRole('button', { name: 'Close panels' });
  // The open drawer covers the scrim's centre; click the exposed left edge.
  if (await scrim.count()) await scrim.click({ position: { x: 8, y: 300 } });
  else await trigger.click();
  await expect(trigger).toHaveAttribute('aria-expanded', 'false');
  await expect(panel).toHaveAttribute('data-open', 'false');

  await page.waitForTimeout(300);
  expect(api.sessionPosts, 'drawer toggling must not request a voice session').toBe(0);
  expect(await orbCanvasIdentity(page), 'drawer toggling must not recreate the orb canvas').toBe(true);

  await writeEvidenceJson('drawer.json', {
    sha: evidenceSha(),
    sessionPosts: api.sessionPosts,
    closedScreenshot,
    openScreenshot,
  });
});

test('Escape closes the drawer and returns focus to its trigger', async ({ page }, testInfo) => {
  await page.setViewportSize(MOBILE);
  await mockApi(page);
  await page.goto('/');
  await waitForHydration(page);

  if (!(await hasThemeControl(page))) {
    annotatePending(testInfo, '#120 Escape handling and focus return');
    test.skip(true, 'Escape handling and focus return are not on this base (pending #120).');
    return;
  }

  const trigger = page.getByRole('button', { name: RIGHT_TRIGGER });
  await trigger.click();
  await expect(trigger).toHaveAttribute('aria-expanded', 'true');

  await page.keyboard.press('Escape');
  await expect(trigger).toHaveAttribute('aria-expanded', 'false');
  await expect(trigger).toBeFocused();
  await writeEvidenceJson('drawer-escape.json', { sha: evidenceSha(), focusReturned: true });
});

test('keyboard-only path reaches the orb and starts exactly one session', async ({ page }) => {
  await page.setViewportSize(DESKTOP);
  const api = await mockApi(page);
  await page.goto('/');
  await waitForHydration(page);

  let reached = false;
  for (let tab = 0; tab < 40 && !reached; tab += 1) {
    await page.keyboard.press('Tab');
    reached = await page.evaluate(() => document.activeElement?.classList.contains('orb-button') ?? false);
  }
  expect(reached, 'Tab order must reach the voice orb').toBe(true);

  const focus = await page.locator('.orb-button').evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      outlineStyle: style.outlineStyle,
      outlineWidth: style.outlineWidth,
      outlineColor: style.outlineColor,
    };
  });
  expect(focus.outlineStyle).not.toBe('none');
  expect(Number.parseFloat(focus.outlineWidth)).toBeGreaterThanOrEqual(2);

  await page.keyboard.press('Enter');
  await expect(page.locator('[data-code="PIPELINE_NOT_CONFIGURED"]')).toBeVisible();
  expect(api.sessionPosts).toBe(1);

  const screenshot = await capture(page, 'keyboard-focus.png');
  await writeEvidenceJson('keyboard.json', { sha: evidenceSha(), focus, sessionPosts: api.sessionPosts, screenshot });
});

const REDUCED_THEMES: EvidenceTheme[] = ['light', 'dark'];

for (const theme of REDUCED_THEMES) {
  test(`reduced motion renders a static, filled orb (${theme})`, async ({ page }) => {
    await page.setViewportSize(DESKTOP);
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await mockApi(page);
    await page.goto('/?phase=listening');
    await waitForHydration(page);
    await selectTheme(page, theme);

    const orb = page.locator('.orb-button');
    await expect(orb).toHaveAttribute('data-renderer', 'static');
    await expect(orb.locator('.orb-static')).toBeVisible();
    await expect(orb.locator('canvas')).toHaveCount(0);

    const fill = await orb.locator('.orb-static').evaluate((element) => {
      const style = getComputedStyle(element);
      const box = element.getBoundingClientRect();
      return {
        borderRadius: style.borderRadius,
        backgroundImage: style.backgroundImage,
        backgroundColor: style.backgroundColor,
        width: Math.round(box.width),
        height: Math.round(box.height),
      };
    });
    expect(fill.backgroundImage !== 'none' || fill.backgroundColor !== 'rgba(0, 0, 0, 0)').toBe(true);
    expect(Number.parseFloat(fill.borderRadius)).toBeGreaterThanOrEqual(50);
    expect(Math.abs(fill.width - fill.height), 'static orb must stay circular').toBeLessThanOrEqual(1);

    const first = await orb.screenshot();
    await page.waitForTimeout(900);
    const second = await orb.screenshot();
    expect(first.equals(second), 'reduced-motion orb must not animate').toBe(true);

    const screenshot = await captureLocator(page, '.orb-button', `reduced-motion-${theme}.png`);
    await writeEvidenceJson(`reduced-motion-${theme}.json`, { sha: evidenceSha(), theme, fill, screenshot });
  });
}

test('Wordle daily evidence is not available on this base', async ({ browserName }, testInfo) => {
  void browserName;
  annotatePending(testInfo, '#108 data/API (PR #118) and #109 UI (PR #125) are not merged into this base');
  test.skip(
    true,
    'Wordle is absent from integration/living-cloud 6f05d3a. One-fetch and no-pre-terminal-answer proof must come from #108 tests (PR #118); no claim is made here.',
  );
});

test('voice-switch audible ACK requires a real provider run', async ({ browserName }, testInfo) => {
  void browserName;
  annotatePending(testInfo, '#112 voice switch (PR #124) plus a real LiveKit/ElevenLabs run');
  test.skip(
    true,
    'Unrun: same-room audible ACK needs a real provider and #112 merge. Text tests cannot prove audible voice change.',
  );
});
