import { describe, expect, it } from 'vitest';
import { mixHexColors, parseHexColor } from './render/colors';
import {
  INITIAL_STATE,
  ORB_OVERLAYS,
  VOICE_PHASES,
  deriveOrbPresentation,
  nextEpoch,
  resolvePhaseColor,
  type OrbPresentation,
  type VoiceUiState,
} from './orbState';
import { ORB_PALETTES, ORB_STATE_COLORS, PHASE_PALETTE_WEIGHTS, type ThemeName } from './palette';

const THEMES: readonly ThemeName[] = ['light', 'dark'];

function state(patch: Partial<VoiceUiState>): VoiceUiState {
  return { ...INITIAL_STATE, ...patch };
}

function kitColors(theme: ThemeName): readonly string[] {
  return [...ORB_PALETTES[theme], ...Object.values(ORB_STATE_COLORS[theme])].map((color) => color.toLowerCase());
}

function expectedPrimary(theme: ThemeName, presentation: OrbPresentation, overlay: VoiceUiState['overlay']): string {
  if (presentation.effectivePhase === 'error') return ORB_STATE_COLORS[theme].error;
  const base = resolvePhaseColor(theme, presentation.effectivePhase);
  if (overlay === 'muted' && presentation.effectivePhase !== 'disconnected') {
    return mixHexColors(base, ORB_STATE_COLORS[theme].muted, 0.5);
  }
  return base;
}

function expectParseable(color: string, label: string) {
  const parsed = parseHexColor(color);
  expect(parsed, `${label} ${color}`).not.toBeNull();
  expect(parsed!.every((channel) => Number.isFinite(channel) && channel >= 0 && channel <= 1)).toBe(true);
}

describe('theme-aware palette mapping', () => {
  it('weights every living phase with in-range slots that sum to one', () => {
    for (const theme of THEMES) {
      for (const phase of VOICE_PHASES) {
        const weights = PHASE_PALETTE_WEIGHTS[theme][phase];
        expect(weights.length, `${theme}/${phase}`).toBeGreaterThan(0);
        const total = weights.reduce((sum, entry) => sum + entry.weight, 0);
        expect(total, `${theme}/${phase}`).toBeCloseTo(1, 6);
        for (const entry of weights) {
          expect(entry.weight).toBeGreaterThan(0);
          expect(entry.index).toBeGreaterThanOrEqual(0);
          expect(entry.index).toBeLessThan(ORB_PALETTES[theme].length);
        }
      }
    }
  });

  it('maps the same phase to different colours in each theme', () => {
    for (const phase of ['idle', 'connecting', 'listening', 'thinking', 'speaking'] as const) {
      expect(resolvePhaseColor('light', phase)).not.toBe(resolvePhaseColor('dark', phase));
    }
    expect(resolvePhaseColor('light', 'disconnected')).not.toBe(resolvePhaseColor('dark', 'disconnected'));
  });

  it('fades disconnected toward the theme slate instead of a live hue', () => {
    for (const theme of THEMES) {
      const disconnected = parseHexColor(resolvePhaseColor(theme, 'disconnected'))!;
      const live = parseHexColor(resolvePhaseColor(theme, 'listening'))!;
      const spread = (rgb: readonly number[]) => Math.max(...rgb) - Math.min(...rgb);
      expect(spread(disconnected)).toBeLessThan(spread(live));
    }
  });

  it('carries the resolved palette weights on the presentation', () => {
    for (const theme of THEMES) {
      for (const phase of VOICE_PHASES) {
        const presentation = deriveOrbPresentation(state({ phase }), theme);
        expect(presentation.theme).toBe(theme);
        expect(presentation.paletteWeights).toEqual(PHASE_PALETTE_WEIGHTS[theme][phase]);
      }
    }
  });
});

describe('theme-change invariance', () => {
  it('changes only presentation colours and weights between themes', () => {
    for (const phase of VOICE_PHASES) {
      for (const overlay of ORB_OVERLAYS) {
        const input = state({ phase, overlay, micActive: phase === 'listening' });
        const before = JSON.stringify(input);
        const light = deriveOrbPresentation(input, 'light');
        const dark = deriveOrbPresentation(input, 'dark');

        expect(light.effectivePhase).toBe(dark.effectivePhase);
        expect(light.motionPreset).toEqual(dark.motionPreset);
        expect(light.statusLabel).toEqual(dark.statusLabel);
        expect(light.ariaLabel).toEqual(dark.ariaLabel);
        expect(light.enabledActions).toEqual(dark.enabledActions);
        expect(light.showMicSlash).toBe(dark.showMicSlash);
        expect(light.accentOnly).toBe(dark.accentOnly);
        expect(light.paletteWeights).toEqual(PHASE_PALETTE_WEIGHTS.light[phase]);
        expect(dark.paletteWeights).toEqual(PHASE_PALETTE_WEIGHTS.dark[phase]);
        expect(light.primaryColor).toBe(expectedPrimary('light', light, overlay));
        expect(dark.primaryColor).toBe(expectedPrimary('dark', dark, overlay));

        // The state object is frozen in time: derivation must not mutate it or
        // smuggle the theme into session identity.
        expect(JSON.stringify(input)).toBe(before);
        expect('theme' in input).toBe(false);
      }
    }
  });

  it('keeps session identity and epoch untouched by a theme change', () => {
    const input = state({ phase: 'speaking', overlay: 'none', sessionId: 'session-7' });
    const tag = { sessionId: input.sessionId, epoch: 4 };
    deriveOrbPresentation(input, 'dark');
    expect(input.sessionId).toBe('session-7');
    expect(tag).toEqual({ sessionId: 'session-7', epoch: 4 });
    expect(nextEpoch(tag.epoch)).toBe(5);
  });
});

describe('color contract', () => {
  it('emits only parseable literal colours for every theme/phase/overlay pair', () => {
    for (const theme of THEMES) {
      for (const phase of VOICE_PHASES) {
        for (const overlay of ORB_OVERLAYS) {
          const presentation = deriveOrbPresentation(state({ phase, overlay }), theme);
          expectParseable(presentation.primaryColor, `${theme}/${phase}/${overlay} primary`);
          if (presentation.accent) {
            expectParseable(presentation.accent, `${theme}/${phase}/${overlay} accent`);
          }
        }
      }
    }
  });

  it('draws accents and error colours from the theme kit', () => {
    for (const theme of THEMES) {
      const allowed = kitColors(theme);
      for (const phase of VOICE_PHASES) {
        for (const overlay of ORB_OVERLAYS) {
          const presentation = deriveOrbPresentation(state({ phase, overlay }), theme);
          if (presentation.accent) {
            expect(allowed, `${theme}/${phase}/${overlay}`).toContain(presentation.accent.toLowerCase());
          }
        }
      }
      expect(deriveOrbPresentation(state({ overlay: 'error' }), theme).primaryColor.toLowerCase()).toBe(
        ORB_STATE_COLORS[theme].error.toLowerCase(),
      );
    }
  });

  it('uses the numeric muted blend rather than a near-black parse', () => {
    for (const theme of THEMES) {
      for (const phase of ['idle', 'connecting', 'listening', 'thinking', 'speaking'] as const) {
        const live = resolvePhaseColor(theme, phase);
        const presentation = deriveOrbPresentation(state({ phase, overlay: 'muted' }), theme);
        expect(presentation.primaryColor).toBe(mixHexColors(live, ORB_STATE_COLORS[theme].muted, 0.5));
        expect(presentation.primaryColor).not.toBe(live);
        const channels = parseHexColor(presentation.primaryColor)!;
        expect(channels.reduce((sum, channel) => sum + channel, 0)).toBeGreaterThan(0.3);
      }
    }
  });

  it('keeps muted speaking/thinking labels and live-state precedence', () => {
    for (const theme of THEMES) {
      for (const phase of ['speaking', 'thinking'] as const) {
        const presentation = deriveOrbPresentation(state({ phase, overlay: 'muted', micActive: false }), theme);
        expect(presentation.effectivePhase).toBe(phase);
        expect(presentation.statusLabel.en).not.toBe('Microphone muted');
        expect(presentation.statusLabel.en).toContain(phase === 'speaking' ? 'Speaking' : 'Thinking');
        expect(presentation.showMicSlash).toBe(true);
      }
    }
  });
});
