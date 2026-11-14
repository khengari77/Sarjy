import type { ThemeName } from '../palette';
import type { MotionPreset, RendererTier } from '../orbState';

export interface OrbRenderState {
  /** Resolved shell theme; the renderer never reads data-theme itself. */
  theme: ThemeName;
  color: string;
  accent: string | null;
  motion: MotionPreset;
  /**
   * True only while the presentation is idle. The renderer then adds its
   * synthetic time-based breathing envelope; live phases are driven by levels.
   */
  idle: boolean;
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
