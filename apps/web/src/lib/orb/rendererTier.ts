/* ------------------------------------------------------------------ */
/* Renderer tier selection (spec 4.7)                                  */
/* ------------------------------------------------------------------ */

export type RendererTier = 'enhanced' | 'light' | 'static';

export interface RendererEnvironment {
  reducedMotion: boolean;
  webglAvailable: boolean;
  /** Measured frames per second, when known. */
  fps?: number;
}

export const MIN_FPS_TARGET = 30;

export function selectRendererTier(environment: RendererEnvironment): RendererTier {
  if (environment.reducedMotion) return 'static';
  if (!environment.webglAvailable) return 'light';
  if (typeof environment.fps === 'number' && environment.fps > 0 && environment.fps < MIN_FPS_TARGET) return 'light';
  return 'enhanced';
}

/**
 * Downward-only renderer fallback. A recovered frame rate never promotes the
 * tier again, which avoids oscillating between canvas and WebGL mid-session.
 */
export function nextRendererTier(tier: RendererTier, fps: number): RendererTier {
  if (tier === 'static') return 'static';
  if (!Number.isFinite(fps) || fps <= 0) return tier;
  if (fps < 12) return 'static';
  if (fps < MIN_FPS_TARGET) return 'light';
  return tier;
}
