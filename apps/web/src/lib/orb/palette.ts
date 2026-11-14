/**
 * Theme-aware Living Cloud palette contract.
 *
 * Values are copied verbatim from the v2.1 design kit
 * (`design/tokens/tokens.json`, `themes.light.orbPalette` and
 * `themes.dark.orbPalette`). This module is pure and shared by the orb state
 * derivation (#38) and the renderer (#40) so neither invents its own colours.
 *
 * The renderer requires literal 6-digit hex. Never pass CSS `color-mix()` or
 * named colours across this boundary (regression B01).
 */

export type ThemeName = 'light' | 'dark';

/** 4-6 blended colours per theme: 2 dominant + 1-3 accents. */
export const ORB_PALETTES: Record<ThemeName, readonly string[]> = {
  light: ['#9cdbff', '#7cdcc7', '#ffd37e', '#ff9ab0', '#b5a0ff', '#d6f6ff'],
  dark: ['#548fff', '#3b6fff', '#9a74f7', '#baa7ff', '#57d7d2', '#f67c9f'],
};

export function orbPalette(theme: ThemeName): readonly string[] {
  return ORB_PALETTES[theme];
}

/** Dominant pair used for the cloud body; the remainder are accents. */
export function orbDominant(theme: ThemeName): readonly [string, string] {
  const palette = ORB_PALETTES[theme];
  return [palette[0], palette[1]];
}

/** Accent colours, in stable order, for wisps and highlights. */
export function orbAccents(theme: ThemeName): readonly string[] {
  return ORB_PALETTES[theme].slice(2);
}

/** Page background per theme, for contrast/fallback decisions. */
export const THEME_BACKGROUND: Record<ThemeName, string> = {
  light: '#eaf6ff',
  dark: '#080f29',
};
