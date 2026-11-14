import { describe, expect, it } from 'vitest';
import { ORB_PALETTES, orbAccents, orbDominant } from '../palette';
import { parseHexColor } from './colors';
import { paletteFor, resolveTheme } from './theme';

describe('renderer theme resolution', () => {
  it('accepts only the two stamped theme names', () => {
    expect(resolveTheme('light')).toBe('light');
    expect(resolveTheme('dark')).toBe('dark');
    for (const value of [undefined, '', 'Light', 'cosmic', 'dark ']) {
      expect(resolveTheme(value)).toBe('light');
    }
  });
});

describe('Living Cloud palette contract (B01)', () => {
  it('provides four to six literal hex colours per theme', () => {
    for (const theme of ['light', 'dark'] as const) {
      const palette = ORB_PALETTES[theme];
      expect(palette.length).toBeGreaterThanOrEqual(4);
      expect(palette.length).toBeLessThanOrEqual(6);
      for (const value of palette) {
        expect(value, `${theme} ${value}`).toMatch(/^#[0-9a-f]{6}$/);
        const channels = parseHexColor(value);
        expect(channels, `${theme} ${value}`).not.toBeNull();
        expect(channels!.every((channel) => Number.isFinite(channel) && channel >= 0 && channel <= 1)).toBe(true);
      }
    }
  });

  it('keeps dominant and accent slices stable and disjoint', () => {
    for (const theme of ['light', 'dark'] as const) {
      const dominant = orbDominant(theme);
      const accents = orbAccents(theme);
      expect(dominant).toHaveLength(2);
      expect(dominant[0]).not.toBe(dominant[1]);
      expect(accents.length).toBeGreaterThanOrEqual(2);
      expect([...dominant, ...accents]).toEqual([...ORB_PALETTES[theme]]);
    }
  });

  it('feeds the renderer exactly the contract palette', () => {
    expect(paletteFor('light')).toBe(ORB_PALETTES.light);
    expect(paletteFor('dark')).toBe(ORB_PALETTES.dark);
  });
});
