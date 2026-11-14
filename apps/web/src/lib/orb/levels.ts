/**
 * Level mapping shared by the presentation and the renderer.
 *
 * Browser audio levels are untrusted: analyser maths, a fake runtime in tests,
 * or device quirks can produce values outside 0..1. Everything the orb renders
 * is clamped here so a bad sample can never scale the silhouette past the
 * surface or expose a raw reading in `data-*`.
 */

export type LevelPhase = 'idle' | 'connecting' | 'listening' | 'thinking' | 'speaking' | 'disconnected' | 'error';

/** Normalise a level, mapping non-finite input to silence. */
export function clampLevel(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

/**
 * Which measured level drives the orb, by effective phase.
 *
 * `connecting` intentionally reacts to the real microphone while the session
 * is still being admitted; the connecting preview feeds this same field. The
 * idle envelope is synthetic and lives in the renderer, so idle maps to zero.
 */
export function levelForPhase(phase: LevelPhase, micLevel: number, outputLevel: number): number {
  if (phase === 'listening' || phase === 'connecting') return clampLevel(micLevel);
  if (phase === 'speaking') return clampLevel(outputLevel);
  return 0;
}
