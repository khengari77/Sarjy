import { expect, test } from '@playwright/test';
import {
  capture,
  captureLocator,
  evidenceSha,
  annotatePending,
  measureContrast,
  mockApi,
  readColorTokens,
  selectTheme,
  waitForHydration,
  writeEvidenceJson,
  type EvidenceTheme,
} from './support/evidence';

const DESKTOP = { width: 1440, height: 900 };
const MOBILE = { width: 390, height: 844 };
const TABLET = { width: 768, height: 1024 };
const THEMES: EvidenceTheme[] = ['light', 'dark'];

const ORB_STATES = [
  { name: 'idle', query: '', phase: 'idle', status: 'Ready' },
  { name: 'listening', query: '?phase=listening', phase: 'listening', status: 'Listening…' },
  { name: 'thinking', query: '?phase=thinking', phase: 'thinking', status: 'Thinking…' },
  { name: 'speaking', query: '?phase=speaking', phase: 'speaking', status: 'Speaking…' },
  { name: 'muted', query: '?overlay=muted', phase: 'idle', status: 'Microphone muted' },
  { name: 'error', query: '?overlay=error', phase: 'error', status: 'Something went wrong' },
  { name: 'disconnected', query: '?phase=disconnected', phase: 'disconnected', status: 'Disconnected' },
] as const;

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('sarjy-language', 'en'));
});

test('layout default at 1440×900: left dock open, right drawer closed', async ({ page }, testInfo) => {
  await page.setViewportSize(DESKTOP);
  await mockApi(page);
  await page.goto('/');
  await waitForHydration(page);
  await page.waitForTimeout(250);
  annotatePending(testInfo, '#120 flips the desktop default to left-open/right-closed');

  const left = page.getByRole('button', { name: 'Show conversations' });
  const right = page.getByRole('button', { name: 'Show memory and settings' });
  const rightPanel = page.locator('aside[aria-label="Memory and settings"]');

  const observed = {
    sha: evidenceSha(),
    viewport: DESKTOP,
    leftExpanded: await left.getAttribute('aria-expanded'),
    rightExpanded: await right.getAttribute('aria-expanded'),
    rightPanelVisible: await rightPanel.isVisible(),
    screenshot: await capture(page, 'layout-1440x900-default.png'),
  };
  await writeEvidenceJson('layout-desktop.json', observed);

  expect.soft(observed.leftExpanded, 'desktop left dock must start open').toBe('true');
  expect.soft(observed.rightExpanded, 'desktop right drawer must start closed').toBe('false');
  expect.soft(observed.rightPanelVisible, 'desktop right drawer must not be visible').toBe(false);
});

test('layout default at tablet and mobile: both drawers closed', async ({ page }) => {
  await mockApi(page);
  const observed: Record<string, unknown>[] = [];

  for (const [name, viewport] of [
    ['tablet', TABLET],
    ['mobile', MOBILE],
  ] as const) {
    await page.setViewportSize(viewport);
    await page.goto('/');
    await waitForHydration(page);
    await page.waitForTimeout(250);

    const left = page.getByRole('button', { name: 'Show conversations' });
    const right = page.getByRole('button', { name: 'Show memory and settings' });
    const entry = {
      viewport: { name, ...viewport },
      leftExpanded: await left.getAttribute('aria-expanded'),
      rightExpanded: await right.getAttribute('aria-expanded'),
      screenshot: await capture(page, `layout-${viewport.width}x${viewport.height}-default.png`),
    };
    observed.push(entry);
    expect.soft(entry.leftExpanded, `${name} left drawer must start closed`).toBe('false');
    expect.soft(entry.rightExpanded, `${name} right drawer must start closed`).toBe('false');
  }

  await writeEvidenceJson('layout-narrow.json', { sha: evidenceSha(), viewports: observed });
});

for (const [name, viewport] of [
  ['desktop', DESKTOP],
  ['mobile', MOBILE],
] as const) {
  test(`theme screenshots at ${name}: light and dark`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockApi(page);
    const applications: Record<string, string> = {};
    const backgrounds: Record<string, string> = {};

    for (const theme of THEMES) {
      await page.goto('/');
      await waitForHydration(page);
      applications[theme] = await selectTheme(page, theme);
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      // Let the 280ms colour transition settle before measuring and capturing.
      await page.waitForTimeout(400);
      backgrounds[theme] = (await readColorTokens(page)).bgApp;
      await capture(page, `theme-${name}-${theme}.png`);
    }

    expect(backgrounds.light, 'light theme must render a different app background').not.toBe(backgrounds.dark);
    await writeEvidenceJson(`theme-screenshots-${name}.json`, {
      sha: evidenceSha(),
      viewport: { name, ...viewport },
      applications,
      backgrounds,
    });
  });
}

test('orb states: seven fixture-driven states in both themes', async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize(DESKTOP);
  await mockApi(page);
  const captured: { theme: EvidenceTheme; state: string; phase: string; screenshot: string }[] = [];

  for (const theme of THEMES) {
    for (const state of ORB_STATES) {
      await page.goto(`/${state.query}`);
      await waitForHydration(page);
      await selectTheme(page, theme);
      await expect(page.locator('.orb-button')).toHaveAttribute('data-phase', state.phase);
      await expect(page.locator('.status-line')).toContainText(state.status);
      captured.push({
        theme,
        state: state.name,
        phase: state.phase,
        screenshot: await captureLocator(page, '.orb-button', `orb-${theme}-${state.name}.png`),
      });
    }
  }

  await writeEvidenceJson('orb-states.json', {
    sha: evidenceSha(),
    source: 'DEV ?phase=/?overlay= fixtures in bootStudio; runtime-transition evidence is separate',
    captured,
  });
  expect(captured).toHaveLength(ORB_STATES.length * THEMES.length);
});

for (const theme of THEMES) {
  test(`measured contrast in the ${theme} theme`, async ({ page }) => {
    await page.setViewportSize(DESKTOP);
    await mockApi(page);
    await page.goto('/');
    await waitForHydration(page);
    const application = await selectTheme(page, theme);
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    await page.waitForTimeout(400);

    const tokens = await readColorTokens(page);
    const measurements = measureContrast(tokens);
    const failures = measurements.filter((measurement) => !measurement.pass);

    await writeEvidenceJson(`contrast-${theme}.json`, {
      sha: evidenceSha(),
      theme,
      themeApplication: application,
      viewport: DESKTOP,
      tokens,
      measurements,
      failures,
    });
    await capture(page, `contrast-${theme}.png`);

    expect(failures, `contrast below threshold (${theme}): ${JSON.stringify(failures, null, 2)}`).toEqual([]);
    expect(measurements.length).toBeGreaterThanOrEqual(5);
  });
}
