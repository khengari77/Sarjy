import { describe, expect, it } from 'vitest';
import {
  INITIAL_STATE,
  deriveOrbPresentation,
  isCurrentTag,
  nextEpoch,
  nextRendererTier,
  resolveEffectivePhase,
  selectRendererTier,
  type VoiceUiState,
} from './orbState';

function state(patch: Partial<VoiceUiState>): VoiceUiState {
  return { ...INITIAL_STATE, ...patch };
}

describe('priority resolution', () => {
  it('gives error the highest priority even while speaking', () => {
    expect(resolveEffectivePhase({ phase: 'speaking', overlay: 'error' })).toBe('error');
  });

  it('lets disconnected beat an active voice phase', () => {
    expect(resolveEffectivePhase({ phase: 'speaking', overlay: 'none' })).toBe('speaking');
    expect(resolveEffectivePhase({ phase: 'disconnected', overlay: 'none' })).toBe('disconnected');
  });

  it('keeps memory_work/confirming as accents over the live phase', () => {
    const memory = deriveOrbPresentation(state({ phase: 'speaking', overlay: 'memory_work' }));
    expect(memory.effectivePhase).toBe('speaking');
    expect(memory.primaryColor).toBe('#4add91');
    expect(memory.accent).toBe('#e9b454');
    expect(memory.accentOnly).toBe(true);

    const confirming = deriveOrbPresentation(state({ phase: 'listening', overlay: 'confirming' }));
    expect(confirming.effectivePhase).toBe('listening');
    expect(confirming.primaryColor).toBe('#4da8ff');
    expect(confirming.accent).toBe('#ffa65a');
  });

  it('never blends speaking amber into the base colour', () => {
    const presentation = deriveOrbPresentation(state({ phase: 'speaking', overlay: 'memory_work' }));
    expect(presentation.primaryColor).not.toBe('#e9b454');
  });
});

describe('labels and accessibility', () => {
  it('localises every phase without raw SDK names', () => {
    for (const phase of ['idle', 'connecting', 'listening', 'thinking', 'speaking', 'disconnected'] as const) {
      const presentation = deriveOrbPresentation(state({ phase }));
      expect(presentation.statusLabel.en).not.toBe(phase);
      expect(presentation.statusLabel.ar.length).toBeGreaterThan(0);
      expect(presentation.ariaLabel.en.length).toBeGreaterThan(0);
    }
  });

  it('shows a mic slash and an unmute action when muted', () => {
    const presentation = deriveOrbPresentation(state({ phase: 'listening', overlay: 'muted', micActive: false }));
    expect(presentation.showMicSlash).toBe(true);
    expect(presentation.enabledActions).toContain('unmute');
    expect(presentation.enabledActions).not.toContain('mute');
    expect(presentation.statusLabel.en).toBe('Microphone muted');
  });

  it('keeps the speaking state visible while the microphone is muted', () => {
    const presentation = deriveOrbPresentation(state({ phase: 'speaking', overlay: 'muted', outputLevel: 0.9 }));
    expect(presentation.effectivePhase).toBe('speaking');
    expect(presentation.statusLabel.en).toBe('Speaking…');
    expect(presentation.showMicSlash).toBe(true);
    expect(presentation.enabledActions).toContain('unmute');
    expect(presentation.enabledActions).toContain('interrupt');
  });

  it('exposes only start from idle and never duplicate start', () => {
    expect(deriveOrbPresentation(state({})).enabledActions).toEqual(['start']);
  });
});

describe('motion', () => {
  it('drives listening pulse from mic level and clamps input', () => {
    const quiet = deriveOrbPresentation(state({ phase: 'listening', micLevel: 0 }));
    const loud = deriveOrbPresentation(state({ phase: 'listening', micLevel: 3 }));
    expect(loud.motionPreset.radialPulse).toBeGreaterThanOrEqual(quiet.motionPreset.radialPulse);
    expect(loud.motionPreset.radialPulse).toBeLessThanOrEqual(1);
  });

  it('quiets the orb without changing phase when silent', () => {
    const silent = deriveOrbPresentation(state({ phase: 'speaking', outputLevel: 0 }));
    expect(silent.effectivePhase).toBe('speaking');
    expect(silent.motionPreset.radialPulse).toBeLessThan(0.5);
  });
});

describe('session epoch guard', () => {
  it('accepts the current tag and newer epochs only', () => {
    const current = { sessionId: 'a', epoch: 2 };
    expect(isCurrentTag(current, { sessionId: 'a', epoch: 2 })).toBe(true);
    expect(isCurrentTag(current, { sessionId: 'old', epoch: 1 })).toBe(false);
    expect(isCurrentTag(current, { sessionId: 'b', epoch: 3 })).toBe(true);
    expect(nextEpoch(2)).toBe(3);
  });
});

describe('renderer tier', () => {
  it('selects static for reduced motion and light for missing webgl or low fps', () => {
    expect(selectRendererTier({ reducedMotion: true, webglAvailable: true })).toBe('static');
    expect(selectRendererTier({ reducedMotion: false, webglAvailable: false })).toBe('light');
    expect(selectRendererTier({ reducedMotion: false, webglAvailable: true, fps: 12 })).toBe('light');
    expect(selectRendererTier({ reducedMotion: false, webglAvailable: true, fps: 60 })).toBe('enhanced');
  });
});

describe('renderer fallback', () => {
  it('steps down but never back up', () => {
    expect(nextRendererTier('enhanced', 18)).toBe('light');
    expect(nextRendererTier('light', 18)).toBe('light');
    expect(nextRendererTier('enhanced', 8)).toBe('static');
    expect(nextRendererTier('static', 60)).toBe('static');
    expect(nextRendererTier('enhanced', 60)).toBe('enhanced');
    expect(nextRendererTier('enhanced', Number.NaN)).toBe('enhanced');
  });
});
