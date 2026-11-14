import type { MotionPreset, RendererTier } from '../orbState';

export interface OrbRenderState {
  color: string;
  accent: string | null;
  motion: MotionPreset;
}

export interface OrbRendererHooks {
  getPulse(): number;
  onTier(tier: RendererTier): void;
  onFailed(): void;
}

/**
 * Tier continuity across canvas remounts.
 *
 * A canvas cannot hand out a 2D context once it owns WebGL2, so VoiceOrb
 * replaces the element whenever the tier changes. The replacement must not
 * re-promote itself: this object records that the renderer has been demoted
 * by a measured frame rate or a WebGL failure, and the next surface starts one
 * step down. Reduced-motion tier changes do not set it, so turning reduced
 * motion off can still restore the enhanced tier.
 */
export interface OrbTierState {
  demoted: boolean;
}

export type Rgb = [number, number, number];
