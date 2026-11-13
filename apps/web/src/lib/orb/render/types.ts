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

export type Rgb = [number, number, number];
