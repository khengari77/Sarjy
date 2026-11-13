import { describe, expect, it, vi } from 'vitest';
import {
  INITIAL_STATE,
  ORB_OVERLAYS,
  VOICE_PHASES,
  deriveOrbPresentation,
  isCurrentTag,
  nextEpoch,
  nextRendererTier,
  resolveEffectivePhase,
  resolveOrbPriority,
  resolvePhaseColor,
  selectRendererTier,
  type VoicePhase,
  type VoiceUiState,
} from './orbState';
import { ORB_STATE_COLORS, type ThemeName } from './palette';

const THEMES: readonly ThemeName[] = ['light', 'dark'];

function state(patch: Partial<VoiceUiState>): VoiceUiState {
  return { ...INITIAL_STATE, ...patch };
}

function expectedEffective(phase: VoicePhase, overlay: VoiceUiState['overlay']) {
  if (overlay === 'error') return 'error';
  if (phase === 'disconnected') return 'disconnected';
  return phase;
}

function expectedPriority(phase: VoicePhase, overlay: VoiceUiState['overlay']) {
  if (overlay === 'error') return 'error';
  if (phase === 'disconnected') return 'disconnected';
  if (overlay === 'confirming') return 'confirming';
  if (overlay === 'memory_work') return 'memory_work';
  if (overlay === 'muted') return 'muted';
  return 'phase';
}

describe('priority matrix', () => {
  it('derives error > disconnected > phase for every reachable combination', () => {
    for (const phase of VOICE_PHASES) {
      for (const overlay of ORB_OVERLAYS) {
        expect(resolveEffectivePhase({ phase, overlay }), `${phase}/${overlay}`).toBe(
          expectedEffective(phase, overlay),
        );
        expect(resolveOrbPriority({ phase, overlay }), `${phase}/${overlay}`).toBe(expectedPriority(phase, overlay));
      }
    }
  });

  it('lets the error overlay outrank a disconnected session', () => {
    expect(resolveEffectivePhase({ phase: 'disconnected', overlay: 'error' })).toBe('error');
    expect(resolveOrbPriority({ phase: 'disconnected', overlay: 'error' })).toBe('error');
  });

  it('suppresses accents entirely while disconnected', () => {
    for (const overlay of ['memory_work', 'confirming', 'muted'] as const) {
      for (const theme of THEMES) {
        const presentation = deriveOrbPresentation(state({ phase: 'disconnected', overlay }), theme);
        expect(presentation.effectivePhase).toBe('disconnected');
        expect(presentation.accent).toBeNull();
        expect(presentation.accentOnly).toBe(false);
        expect(presentation.statusLabel.en).toBe('Connection lost');
        expect(presentation.showMicSlash).toBe(false);
        expect(presentation.enabledActions).toEqual(['reconnect']);
      }
    }
  });

  it('keeps memory_work/confirming as accents over a live phase', () => {
    const memory = deriveOrbPresentation(state({ phase: 'speaking', overlay: 'memory_work' }), 'light');
    expect(memory.effectivePhase).toBe('speaking');
    expect(memory.primaryColor).toBe(resolvePhaseColor('light', 'speaking'));
    expect(memory.accent).toBe(ORB_STATE_COLORS.light.memory);
    expect(memory.accentOnly).toBe(true);
    expect(memory.statusLabel.en).toBe('Checking memory…');

    const confirming = deriveOrbPresentation(state({ phase: 'listening', overlay: 'confirming' }), 'light');
    expect(confirming.effectivePhase).toBe('listening');
    expect(confirming.primaryColor).toBe(resolvePhaseColor('light', 'listening'));
    expect(confirming.accent).toBe(ORB_STATE_COLORS.light.confirmation);
    expect(confirming.enabledActions).toContain('end');
    expect(confirming.statusLabel.en).toBe('Confirmation needed');
  });

  it('never blends an accent into the base phase colour', () => {
    for (const theme of THEMES) {
      const memory = deriveOrbPresentation(state({ phase: 'speaking', overlay: 'memory_work' }), theme);
      expect(memory.primaryColor).toBe(resolvePhaseColor(theme, 'speaking'));
      expect(memory.primaryColor).not.toBe(memory.accent);
    }
  });

  it('gives the error state the error colour and retry action', () => {
    for (const theme of THEMES) {
      const presentation = deriveOrbPresentation(state({ phase: 'speaking', overlay: 'error' }), theme);
      expect(presentation.effectivePhase).toBe('error');
      expect(presentation.primaryColor).toBe(ORB_STATE_COLORS[theme].error);
      expect(presentation.accent).toBeNull();
      expect(presentation.accentOnly).toBe(false);
      expect(presentation.statusLabel.en).toBe('Something went wrong');
      expect(presentation.enabledActions).toEqual(['retry']);
    }
  });
});

describe('labels and accessibility', () => {
  it('localises every phase in both themes without raw SDK names', () => {
    const rawSdkStrings = new Set([
      'connected',
      'connecting',
      'reconnecting',
      'disconnected',
      'initializing',
      'idle',
      'thinking',
      'speaking',
      'listening',
      'agent_state',
    ]);

    for (const theme of THEMES) {
      for (const phase of VOICE_PHASES) {
        const presentation = deriveOrbPresentation(state({ phase }), theme);
        expect(presentation.statusLabel.en).not.toBe(phase);
        expect(presentation.statusLabel.en.toLowerCase()).not.toBe(phase);
        expect(rawSdkStrings.has(presentation.statusLabel.en.toLowerCase())).toBe(false);
        expect(presentation.statusLabel.ar.length).toBeGreaterThan(0);
        expect(presentation.ariaLabel.en.length).toBeGreaterThan(0);
        expect(presentation.ariaLabel.ar.length).toBeGreaterThan(0);
        expect(presentation.ariaLabel.en).not.toBe(phase);
      }
    }
  });

  it('provides complete status and aria text for every theme/phase/overlay pair', () => {
    for (const theme of THEMES) {
      for (const phase of VOICE_PHASES) {
        for (const overlay of ORB_OVERLAYS) {
          const presentation = deriveOrbPresentation(state({ phase, overlay }), theme);
          const id = `${theme}/${phase}/${overlay}`;
          expect(presentation.statusLabel.en.trim().length, id).toBeGreaterThan(0);
          expect(presentation.statusLabel.ar.trim().length, id).toBeGreaterThan(0);
          expect(presentation.ariaLabel.en.trim().length, id).toBeGreaterThan(0);
          expect(presentation.ariaLabel.ar.trim().length, id).toBeGreaterThan(0);
          expect(presentation.enabledActions.length, id).toBeGreaterThan(0);
          expect(new Set(presentation.enabledActions).size, id).toBe(presentation.enabledActions.length);
          expect(presentation.theme, id).toBe(theme);
        }
      }
    }
  });

  it('names the overlay in the aria label while keeping the live phase', () => {
    const presentation = deriveOrbPresentation(state({ phase: 'listening', overlay: 'muted' }), 'dark');
    expect(presentation.ariaLabel.en).toContain('Microphone muted');
    expect(presentation.ariaLabel.en).toContain('Listening');
    expect(presentation.ariaLabel.ar).toContain('الميكروفون مكتوم');
    expect(presentation.ariaLabel.ar).toContain('أستمع');
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

  it('exposes only start from idle and never duplicates start', () => {
    expect(deriveOrbPresentation(state({})).enabledActions).toEqual(['start']);
  });
});

describe('phase vocabulary scope', () => {
  it('contains no onboarding or Wordle phase', () => {
    expect(VOICE_PHASES).toEqual(['idle', 'connecting', 'listening', 'thinking', 'speaking', 'disconnected']);
    for (const forbidden of ['onboarding', 'wordle', 'game']) {
      expect(VOICE_PHASES as readonly string[]).not.toContain(forbidden);
    }
  });

  it('never fabricates a phase for a game-only action', () => {
    const presentation = deriveOrbPresentation(state({ phase: 'idle' }), 'light');
    expect(presentation.effectivePhase).toBe('idle');
    expect(presentation.statusLabel.en).toBe('Ready');
  });
});

describe('purity', () => {
  it('never performs network or storage work during derivation', () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch');
    for (const theme of THEMES) {
      for (const phase of VOICE_PHASES) {
        for (const overlay of ORB_OVERLAYS) {
          deriveOrbPresentation(state({ phase, overlay, micLevel: 0.4 }), theme);
        }
      }
    }
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
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
