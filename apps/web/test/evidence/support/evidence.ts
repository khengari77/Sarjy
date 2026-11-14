import { execSync } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Page } from '@playwright/test';

export const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../../../../..');
export const FALLBACK_SHA = 'unknown-sha';

/** Exact revision under test. The runner pins this before Playwright starts. */
export function evidenceSha(): string {
  const pinned = process.env.SARJY_EVIDENCE_SHA?.trim();
  if (pinned) return pinned;
  try {
    return execSync('git rev-parse HEAD', { cwd: REPO_ROOT, encoding: 'utf8' }).trim();
  } catch {
    return FALLBACK_SHA;
  }
}

/** Ignored artifact root: test-results/evidence/<sha>/ unless the runner overrides it. */
export function evidenceRoot(): string {
  const configured = process.env.SARJY_EVIDENCE_DIR?.trim();
  return configured ? resolve(configured) : join(REPO_ROOT, 'test-results', 'evidence', evidenceSha());
}

export interface ApiMockState {
  sessionPosts: number;
  endPosts: number;
}

/**
 * Deterministic API boundary for visual evidence. No provider or database is
 * touched: the session endpoint returns an explicit unconfigured error, which
 * is enough to prove a gesture did (or did not) request a voice session.
 */
export async function mockApi(page: Page): Promise<ApiMockState> {
  const state: ApiMockState = { sessionPosts: 0, endPosts: 0 };

  await page.addInitScript(() => {
    const stream = { getTracks: () => [], getAudioTracks: () => [] } as unknown as MediaStream;
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: async () => stream },
    });
  });

  // Mark the post-mount bootstrap request. This is a reliable hydration signal
  // even when reduced motion keeps the orb renderer statically rendered.
  await page.addInitScript(() => {
    const original = window.fetch.bind(window);
    window.fetch = async (...args: Parameters<typeof fetch>) => {
      const response = await original(...args);
      const input = args[0];
      const url = typeof input === 'string' ? input : input instanceof Request ? input.url : String(input);
      if (url.includes('/api/v1/demo-session')) {
        (window as unknown as { __sarjyEvidenceBooted?: boolean }).__sarjyEvidenceBooted = true;
      }
      return response;
    };
  });

  await page.route('**/api/v1/demo-session', (route) => route.fulfill({ json: { id: 'evidence-owner' } }));
  await page.route('**/api/v1/conversations', (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ json: [] })
      : route.fulfill({ json: { id: 'c-evidence', title: 'Evidence conversation', revision: 0 } }),
  );
  await page.route('**/api/v1/conversations/*/turns', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/v1/conversations/*/tool-calls', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/v1/voices*', (route) => route.fulfill({ json: { voices: [] } }));
  await page.route('**/api/v1/voice/sessions/*/end', async (route) => {
    state.endPosts += 1;
    await route.fulfill({ json: {} });
  });
  await page.route('**/api/v1/voice/sessions', async (route) => {
    state.sessionPosts += 1;
    await route.fulfill({ status: 503, json: { detail: { code: 'PIPELINE_NOT_CONFIGURED' } } });
  });

  return state;
}

/** Wait until Svelte has hydrated the shell and the mounted bootstrap ran. */
export async function waitForHydration(page: Page): Promise<void> {
  await page.locator('.orb-button').waitFor({ state: 'visible' });
  await page.waitForFunction(
    () => {
      const booted = (window as unknown as { __sarjyEvidenceBooted?: boolean }).__sarjyEvidenceBooted === true;
      const orb = document.querySelector<HTMLButtonElement>('.orb-button');
      return booted && orb !== null && !orb.disabled;
    },
    undefined,
    { timeout: 20_000 },
  );
}

export async function capture(page: Page, name: string): Promise<string> {
  const directory = join(evidenceRoot(), 'screens');
  await mkdir(directory, { recursive: true });
  const file = join(directory, name.endsWith('.png') ? name : `${name}.png`);
  await page.screenshot({ path: file });
  return relative(evidenceRoot(), file).split('\\').join('/');
}

export async function captureLocator(page: Page, selector: string, name: string): Promise<string> {
  const directory = join(evidenceRoot(), 'screens');
  await mkdir(directory, { recursive: true });
  const file = join(directory, name.endsWith('.png') ? name : `${name}.png`);
  await page.locator(selector).screenshot({ path: file });
  return relative(evidenceRoot(), file).split('\\').join('/');
}

export async function writeEvidenceJson(name: string, value: unknown): Promise<string> {
  const directory = evidenceRoot();
  await mkdir(directory, { recursive: true });
  const file = join(directory, name.endsWith('.json') ? name : `${name}.json`);
  await writeFile(file, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
  return relative(directory, file).split('\\').join('/');
}

export type EvidenceTheme = 'light' | 'dark';
export type ThemeApplication = 'toggle' | 'forced-attribute';

/**
 * Prefer the real theme control when the feature is present. On the current
 * integration base (#120 not merged) the control does not exist, so the
 * evidence spec forces the documented `data-theme` attribute instead and
 * records that fact.
 */
export async function selectTheme(page: Page, theme: EvidenceTheme): Promise<ThemeApplication> {
  const toggle = page.locator('.theme-button');
  if ((await toggle.count()) > 0) {
    for (let attempt = 0; attempt < 4; attempt += 1) {
      if ((await page.locator('html').getAttribute('data-theme')) === theme) return 'toggle';
      await toggle.click();
    }
    if ((await page.locator('html').getAttribute('data-theme')) === theme) return 'toggle';
  }
  await page.evaluate((value) => document.documentElement.setAttribute('data-theme', value), theme);
  return 'forced-attribute';
}

export interface ColorTokens {
  bgApp: string;
  bgSidebar: string;
  textPrimary: string;
  muted: string | null;
  modePill: string | null;
  icon: string | null;
  statusLine: string | null;
  sidebarMuted: string | null;
  focusRing: string;
}

export async function readColorTokens(page: Page): Promise<ColorTokens> {
  return page.evaluate(() => {
    const root = getComputedStyle(document.documentElement);
    const token = (name: string) => root.getPropertyValue(name).trim();
    const color = (selector: string) => {
      const element = document.querySelector(selector);
      return element ? getComputedStyle(element).color : null;
    };
    return {
      bgApp: token('--bg-app'),
      bgSidebar: token('--bg-sidebar'),
      textPrimary: getComputedStyle(document.body).color,
      muted: color('.brand span'),
      modePill: color('.mode-pill'),
      icon: color('.icon-button'),
      statusLine: color('.status-line'),
      sidebarMuted: color('.sidebar .muted-text'),
      focusRing: token('--focus-ring'),
    };
  });
}

function parseColor(value: string | null): [number, number, number] | null {
  if (!value) return null;
  const hex = value.trim().match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
  if (hex) {
    const digits = hex[1].length === 3 ? [...hex[1]].map((digit) => digit + digit).join('') : hex[1];
    return [0, 2, 4].map((offset) => parseInt(digits.slice(offset, offset + 2), 16)) as [number, number, number];
  }
  const rgb = value.trim().match(/^rgba?\(([^)]+)\)$/i);
  if (!rgb) return null;
  const parts = rgb[1]
    .split(/[,/\s]+/)
    .filter(Boolean)
    .slice(0, 3)
    .map(Number);
  if (parts.length < 3 || parts.some((part) => !Number.isFinite(part))) return null;
  return parts as [number, number, number];
}

function luminance(color: [number, number, number]): number {
  const [red, green, blue] = color.map((channel) => {
    const value = channel / 255;
    return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
}

export function contrastRatio(foreground: string | null, background: string | null): number | null {
  const fg = parseColor(foreground);
  const bg = parseColor(background);
  if (!fg || !bg) return null;
  const [high, low] = [luminance(fg), luminance(bg)].sort((left, right) => right - left);
  return Math.round(((high + 0.05) / (low + 0.05)) * 100) / 100;
}

export type ContrastKind = 'text' | 'large' | 'ui';
export interface ContrastMeasurement {
  id: string;
  kind: ContrastKind;
  foreground: string;
  background: string;
  ratio: number | null;
  threshold: number;
  pass: boolean;
}

export function measureContrast(tokens: ColorTokens): ContrastMeasurement[] {
  const pairs: { id: string; kind: ContrastKind; foreground: string | null; background: string; threshold: number }[] =
    [
      { id: 'body-text', kind: 'text', foreground: tokens.textPrimary, background: tokens.bgApp, threshold: 4.5 },
      { id: 'brand-muted', kind: 'text', foreground: tokens.muted, background: tokens.bgApp, threshold: 4.5 },
      { id: 'mode-pill', kind: 'text', foreground: tokens.modePill, background: tokens.bgApp, threshold: 4.5 },
      { id: 'icon-glyph', kind: 'ui', foreground: tokens.icon, background: tokens.bgApp, threshold: 3 },
      { id: 'status-line', kind: 'text', foreground: tokens.statusLine, background: tokens.bgApp, threshold: 4.5 },
      {
        id: 'sidebar-muted',
        kind: 'text',
        foreground: tokens.sidebarMuted,
        background: tokens.bgSidebar,
        threshold: 4.5,
      },
    ];
  return pairs.map((pair) => {
    const ratio = contrastRatio(pair.foreground, pair.background);
    return {
      id: pair.id,
      kind: pair.kind,
      foreground: pair.foreground ?? 'unavailable',
      background: pair.background,
      ratio,
      threshold: pair.threshold,
      pass: ratio !== null && ratio >= pair.threshold,
    };
  });
}

/** Canvas identity proves the cosmetic toggle/drawer did not rebuild the orb. */
export async function orbCanvasIdentity(page: Page): Promise<boolean> {
  return page.evaluate(() => {
    const current = document.querySelector('.orb-button canvas');
    const holder = window as Window & { __sarjyEvidenceCanvas?: Element | null };
    if (!holder.__sarjyEvidenceCanvas) holder.__sarjyEvidenceCanvas = current;
    return holder.__sarjyEvidenceCanvas === current;
  });
}

/** True when the #120 theme control is present on this base. */
export async function hasThemeControl(page: Page): Promise<boolean> {
  return (await page.locator('.theme-button').count()) > 0;
}

/** Document an unrun or blocked gate without pretending it passed. */
export function annotatePending(testInfo: import('@playwright/test').TestInfo, description: string): void {
  testInfo.annotations.push({ type: 'pending-dependency', description });
}
