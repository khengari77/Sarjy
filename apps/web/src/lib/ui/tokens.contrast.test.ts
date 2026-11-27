import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

/**
 * WCAG 2.x contrast for the theme tokens. `tokens.css` defines the same
 * semantics three times (dark fallback, explicit light, system-preference
 * light); this suite parses each block so the copies cannot drift below AA.
 */

type ThemeBlock = 'dark' | 'light' | 'system-light';

const THEME_BLOCKS: readonly ThemeBlock[] = ['dark', 'light', 'system-light'];

const CSS = readFileSync(new URL('./tokens.css', import.meta.url), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');

const SELECTORS: Record<ThemeBlock, string> = {
  dark: "[data-theme='dark']",
  light: "[data-theme='light']",
  'system-light': ':root:not([data-theme])',
};

/** Parse one flat rule block the same way the browser would. */
function blockTokens(selector: string): Map<string, string> {
  let found: Map<string, string> | undefined;
  for (const match of CSS.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selectors = (match[1] ?? '').split(',').map((part) => part.trim());
    if (!selectors.includes(selector)) continue;
    if (found) throw new Error(`DUPLICATE_THEME_TOKENS:${selector}`);
    const values = new Map<string, string>();
    for (const declaration of (match[2] ?? '').matchAll(/--([\w-]+)\s*:\s*([^;]+);/g)) {
      values.set(`--${declaration[1]}`, (declaration[2] ?? '').trim().toLowerCase());
    }
    found = values;
  }
  if (!found) throw new Error(`MISSING_THEME_TOKENS:${selector}`);
  return found;
}

const TOKENS = Object.fromEntries(THEME_BLOCKS.map((block) => [block, blockTokens(SELECTORS[block])])) as Record<
  ThemeBlock,
  Map<string, string>
>;

function token(block: ThemeBlock, name: string): string {
  const value = TOKENS[block].get(name);
  if (!value) throw new Error(`MISSING_TOKEN:${block}:${name}`);
  return value;
}

function channels(hex: string): [number, number, number] {
  const value = hex.replace('#', '');
  if (!/^[0-9a-f]{6}$/.test(value)) throw new Error(`INVALID_HEX:${hex}`);
  return [0, 2, 4].map((offset) => Number.parseInt(value.slice(offset, offset + 2), 16) / 255) as [
    number,
    number,
    number,
  ];
}

/** WCAG relative luminance. */
function luminance(hex: string): number {
  const linear = channels(hex).map((channel) =>
    channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * linear[0]! + 0.7152 * linear[1]! + 0.0722 * linear[2]!;
}

function contrast(foreground: string, background: string): number {
  const [lighter, darker] = [luminance(foreground), luminance(background)].sort((a, b) => b - a) as [number, number];
  return (lighter + 0.05) / (darker + 0.05);
}

const SURFACES = ['--bg-app', '--bg-sidebar', '--surface-elevated', '--surface-hover', '--surface-raised'] as const;

const ORB_TEXT_TOKENS = [
  '--orb-idle',
  '--orb-listening',
  '--orb-thinking',
  '--orb-speaking',
  '--orb-memory',
  '--orb-confirmation',
  '--orb-error',
  '--orb-offline',
] as const;

const WORDLE_TILE_PAIRS = [
  ['--wordle-correct-text', '--wordle-correct-bg'],
  ['--wordle-present-text', '--wordle-present-bg'],
] as const;

describe.each(THEME_BLOCKS)('%s theme contrast', (block) => {
  it('keeps --on-accent readable on the accent fill', () => {
    expect(contrast(token(block, '--on-accent'), token(block, '--accent')), block).toBeGreaterThanOrEqual(4.5);
  });

  it('keeps --on-accent readable on the accent hover fill', () => {
    expect(contrast(token(block, '--on-accent'), token(block, '--accent-hover')), block).toBeGreaterThanOrEqual(4.5);
  });

  it.each(WORDLE_TILE_PAIRS)('keeps %s on %s at AA', (textToken, backgroundToken) => {
    expect(
      contrast(token(block, textToken), token(block, backgroundToken)),
      `${block} ${textToken}`,
    ).toBeGreaterThanOrEqual(4.5);
  });

  it.each(SURFACES)('keeps --accent-text at AA on %s', (surface) => {
    expect(
      contrast(token(block, '--accent-text'), token(block, surface)),
      `${block} ${surface}`,
    ).toBeGreaterThanOrEqual(4.5);
  });

  it.each(SURFACES)('keeps --status-error-text at AA on %s', (surface) => {
    expect(
      contrast(token(block, '--status-error-text'), token(block, surface)),
      `${block} ${surface}`,
    ).toBeGreaterThanOrEqual(4.5);
  });

  it.each(SURFACES)('keeps --status-success-text at AA on %s', (surface) => {
    expect(
      contrast(token(block, '--status-success-text'), token(block, surface)),
      `${block} ${surface}`,
    ).toBeGreaterThanOrEqual(4.5);
  });

  it.each(ORB_TEXT_TOKENS)('keeps %s at AA on every surface', (orbToken) => {
    for (const surface of SURFACES) {
      expect(
        contrast(token(block, orbToken), token(block, surface)),
        `${block} ${orbToken} ${surface}`,
      ).toBeGreaterThanOrEqual(4.5);
    }
  });
});
