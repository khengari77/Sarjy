/**
 * Synthetic idle motion for the orb.
 *
 * The idle orb breathes because time passes, never because audio arrives.
 * Nothing here touches the microphone, session state, labels or ARIA: the
 * returned amplitudes are presentation-only inputs to the renderer. Keeping
 * the envelope pure makes the "alive but truthful" claim testable.
 */

export interface IdleEnvelope {
  /** 0..1 slow swell; drives silhouette scale and interior opacity. */
  breath: number;
  /** 0..1 drift rate; drives cloud flow. */
  flow: number;
  /** 0..1 small pulse; a second, slower oscillation so motion never loops. */
  pulse: number;
}

export const STILL_ENVELOPE: IdleEnvelope = { breath: 0, flow: 0, pulse: 0 };

const TAU = Math.PI * 2;

function wave(seconds: number, period: number, phase: number): number {
  return 0.5 + 0.5 * Math.sin((TAU * seconds) / period + phase);
}

/**
 * Evaluate the idle envelope at `seconds` since the surface started.
 * `reducedMotion` returns a still envelope, matching the static renderer tier.
 */
export function idleEnvelope(seconds: number, reducedMotion = false): IdleEnvelope {
  if (reducedMotion) return STILL_ENVELOPE;
  const time = Number.isFinite(seconds) ? Math.max(0, seconds) : 0;
  // Three incommensurate periods (7.6s, 19s, 11.3s) keep the envelope from
  // settling into a visible beat while staying slow enough to feel like breath.
  return {
    breath: wave(time, 7.6, 0),
    flow: wave(time, 19, 1.1) * 0.7,
    pulse: wave(time, 11.3, 2.3) * 0.6,
  };
}
