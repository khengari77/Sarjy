import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import {
  ORB_CLOUD_HUES,
  ORB_PALETTES,
  ORB_STATE_COLORS,
  THEME_BACKGROUND,
  orbAccents,
  orbDominant,
  type ThemeName,
} from './palette';
import { parseHexColor } from './render/colors';

const THEMES: readonly ThemeName[] = ['light', 'dark'];

/**
 * The v2.1 design kit values (`themes.*.orbPalette`, the amber accent and the
 * shell's semantic colours). This pins the intent; `tokens.css` is the copy
 * the rest of the app reads.
 */
const KIT: Record<ThemeName, { palette: readonly string[]; memory: string; error: string; muted: string; bg: string }> =
  {
    light: {
      palette: ['#9cdbff', '#7cdcc7', '#ffd37e', '#ff9ab0', '#b5a0ff', '#d6f6ff'],
      memory: '#ffd37e',
      error: '#c94767',
      muted: '#4d678a',
      // Kit value is #eaf6ff; deepened for orb contrast (see tokens.css).
      bg: '#dceaf7',
    },
    dark: {
      palette: ['#548fff', '#3b6fff', '#9a74f7', '#baa7ff', '#57d7d2', '#f67c9f'],
      memory: '#ffd37e',
      error: '#ff7d9d',
      muted: '#a6b6dc',
      bg: '#080f29',
    },
  };

/** Read the flat rule blocks of tokens.css the same way the browser would. */
function themeTokens(attribute: string): Map<string, string> {
  const css = readFileSync(new URL('../ui/tokens.css', import.meta.url), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
  for (const match of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selectors = (match[1] ?? '').split(',').map((selector) => selector.trim());
    if (!selectors.includes(attribute)) continue;
    const values = new Map<string, string>();
    for (const declaration of (match[2] ?? '').matchAll(/--([\w-]+)\s*:\s*([^;]+);/g)) {
      values.set(`--${declaration[1]}`, (declaration[2] ?? '').trim().toLowerCase());
    }
    return values;
  }
  throw new Error(`MISSING_THEME_TOKENS:${attribute}`);
}

const TOKENS: Record<ThemeName, Map<string, string>> = {
  light: themeTokens("[data-theme='light']"),
  dark: themeTokens("[data-theme='dark']"),
};

function token(theme: ThemeName, name: string): string | undefined {
  return TOKENS[theme].get(name);
}

describe('palette mirrors the shell tokens', () => {
  it('reads the same six palette slots as tokens.css', () => {
    for (const theme of THEMES) {
      const slots = [1, 2, 3, 4, 5, 6].map((slot) => token(theme, `--orb-palette-${slot}`));
      expect(ORB_PALETTES[theme], theme).toEqual(slots);
    }
  });

  it('reuses the shell semantic tokens for non-phase colours', () => {
    for (const theme of THEMES) {
      expect(ORB_STATE_COLORS[theme].memory, theme).toBe(token(theme, '--orb-amber'));
      expect(ORB_STATE_COLORS[theme].confirmation, theme).toBe(token(theme, '--orb-amber'));
      expect(ORB_STATE_COLORS[theme].error, theme).toBe(token(theme, '--status-error'));
      expect(ORB_STATE_COLORS[theme].muted, theme).toBe(token(theme, '--text-muted'));
      expect(ORB_STATE_COLORS[theme].disconnected, theme).toBe(token(theme, '--text-muted'));
      expect(THEME_BACKGROUND[theme], theme).toBe(token(theme, '--bg-app'));
    }
  });

  it('keeps the design-kit values so a token edit cannot silently repaint the orb', () => {
    for (const theme of THEMES) {
      expect(ORB_PALETTES[theme], theme).toEqual(KIT[theme].palette);
      expect(ORB_STATE_COLORS[theme].memory, theme).toBe(KIT[theme].memory);
      expect(ORB_STATE_COLORS[theme].error, theme).toBe(KIT[theme].error);
      expect(ORB_STATE_COLORS[theme].muted, theme).toBe(KIT[theme].muted);
      expect(THEME_BACKGROUND[theme], theme).toBe(KIT[theme].bg);
    }
  });

  it('keeps the dark and light palettes distinct so a theme swap is visible', () => {
    ORB_PALETTES.dark.forEach((colour, index) => {
      expect(colour, `slot ${index + 1}`).not.toBe(ORB_PALETTES.light[index]);
    });
  });

  it('emits only literal six-digit hex from every exposed colour', () => {
    const values = [
      ...THEMES.flatMap((theme) => ORB_PALETTES[theme]),
      ...THEMES.flatMap((theme) => Object.values(ORB_STATE_COLORS[theme])),
      ...THEMES.map((theme) => THEME_BACKGROUND[theme]),
    ];
    for (const value of values) {
      expect(value).toMatch(/^#[0-9a-f]{6}$/);
    }
  });

  it('gives the renderer four to six parseable colours per theme', () => {
    for (const theme of THEMES) {
      const palette = ORB_PALETTES[theme];
      expect(palette.length).toBeGreaterThanOrEqual(4);
      expect(palette.length).toBeLessThanOrEqual(6);
      for (const value of palette) {
        const channels = parseHexColor(value);
        expect(channels, `${theme} ${value}`).not.toBeNull();
        expect(channels!.every((channel) => Number.isFinite(channel) && channel >= 0 && channel <= 1)).toBe(true);
      }
    }
  });

  it('keeps dominant and accent slices stable and disjoint', () => {
    for (const theme of THEMES) {
      const dominant = orbDominant(theme);
      const accents = orbAccents(theme);
      expect(dominant).toHaveLength(2);
      expect(dominant[0]).not.toBe(dominant[1]);
      expect(accents.length).toBeGreaterThanOrEqual(2);
      expect([...dominant, ...accents]).toEqual([...ORB_PALETTES[theme]]);
    }
  });

  it('gives the dark cloud its own multi-hue set instead of near-monochrome lavender', () => {
    const hue = (value: string): number => {
      const [r, g, b] = parseHexColor(value)!;
      const max = Math.max(r, g, b);
      const delta = max - Math.min(r, g, b);
      if (delta === 0) return 0;
      const sector = max === r ? ((g - b) / delta) % 6 : max === g ? (b - r) / delta + 2 : (r - g) / delta + 4;
      return (sector * 60 + 360) % 360;
    };
    const hues = ORB_CLOUD_HUES.dark;
    expect(hues).toHaveLength(6);
    // Slot order: cyan, mint, gold, coral, lavender, white-pink core.
    const [cyan, mint, gold, coral, lavender] = hues.slice(0, 5).map(hue);
    expect(cyan).toBeGreaterThan(190);
    expect(cyan).toBeLessThan(225);
    expect(mint).toBeGreaterThan(150);
    expect(mint).toBeLessThan(180);
    expect(gold).toBeGreaterThan(30);
    expect(gold).toBeLessThan(50);
    expect(coral).toBeGreaterThan(335);
    expect(coral).toBeLessThan(350);
    expect(lavender).toBeGreaterThan(250);
    expect(lavender).toBeLessThan(270);
    // The core is bright and pinkish, not another blue.
    const [r, g, b] = parseHexColor(hues[5]!)!;
    expect(Math.min(r, g, b)).toBeGreaterThan(0.85);
    expect(r).toBeGreaterThanOrEqual(g);
    for (const value of [...ORB_CLOUD_HUES.light, ...hues]) expect(value).toMatch(/^#[0-9a-f]{6}$/);
    // Light keeps the kit palette; only the dark cloud diverges from tokens.
    expect(ORB_CLOUD_HUES.light).toEqual(ORB_PALETTES.light);
  });
});
