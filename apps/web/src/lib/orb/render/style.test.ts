import { describe, expect, it } from 'vitest';
import { ORB_SURFACE_STYLE, orbBodyAlpha, orbSurfaceStyle } from './style';

describe('orb surface style', () => {
  it('makes the light body near-opaque at the core', () => {
    expect(orbBodyAlpha('light', 0)).toBeGreaterThanOrEqual(0.9);
    expect(orbBodyAlpha('light', 1)).toBeGreaterThanOrEqual(0.9);
    expect(orbBodyAlpha('light', 1)).toBeLessThanOrEqual(1);
  });

  it('keeps the dark body formula unchanged', () => {
    expect(orbBodyAlpha('dark', 0)).toBeCloseTo(0.7);
    expect(orbBodyAlpha('dark', 1)).toBeCloseTo(0.94);
    expect(ORB_SURFACE_STYLE.dark.ringAlpha).toBe(0);
  });

  it('clamps density and ignores non-finite input', () => {
    expect(orbBodyAlpha('light', -3)).toBe(orbBodyAlpha('light', 0));
    expect(orbBodyAlpha('light', 4)).toBe(orbBodyAlpha('light', 1));
    expect(orbBodyAlpha('light', Number.NaN)).toBe(orbBodyAlpha('light', 0));
  });

  it('gives only light mode boundary rings and a crisper edge', () => {
    expect(orbSurfaceStyle('light').ringAlpha).toBeGreaterThan(0);
    expect(orbSurfaceStyle('light').edgeSoftness).toBeLessThan(orbSurfaceStyle('dark').edgeSoftness);
  });
});
