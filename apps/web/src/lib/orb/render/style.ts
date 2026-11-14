/**
 * Theme-specific surface styling for the Living Cloud.
 *
 * Dark mode keeps the deliberately translucent kit treatment. On the light
 * page the same cloud washed into the sky, so light mode gets a denser body,
 * a tighter silhouette fade and two faint boundaary rings. These are renderer
 * factors, not palette values: hues still come from `palette.ts`.
 */

import type { ThemeName } from '../palette';

export interface OrbSurfaceStyle {
  /** Alpha of the body field before density is added. */
  bodyBase: number;
  /** How much measured density pushes the body toward opaque. */
  densityGain: number;
  /** Multiplier baked into the cached Canvas2D body texture. */
  textureAlpha: number;
  /** Alpha of the two silhouette rings; 0 keeps dark mode ring-free. */
  ringAlpha: number;
  /** Silhouette fade width; smaller is a crisper edge. */
  edgeSoftness: number;
}

export const ORB_SURFACE_STYLE: Record<ThemeName, OrbSurfaceStyle> = {
  dark: { bodyBase: 0.7, densityGain: 0.24, textureAlpha: 1, ringAlpha: 0, edgeSoftness: 0.18 },
  light: { bodyBase: 0.94, densityGain: 0.06, textureAlpha: 1.35, ringAlpha: 0.9, edgeSoftness: 0.12 },
};

export function orbSurfaceStyle(theme: ThemeName): OrbSurfaceStyle {
  return ORB_SURFACE_STYLE[theme];
}

/** Effective body alpha before compositing, used by WebGL and its unit test. */
export function orbBodyAlpha(theme: ThemeName, density: number): number {
  const style = orbSurfaceStyle(theme);
  const clamped = Number.isFinite(density) ? Math.min(1, Math.max(0, density)) : 0;
  return style.bodyBase + style.densityGain * clamped;
}
