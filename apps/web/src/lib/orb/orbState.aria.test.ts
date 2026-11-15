import { describe, expect, it } from 'vitest';
import { INITIAL_STATE, ORB_OVERLAYS, VOICE_PHASES, deriveOrbPresentation, type OrbAction } from './orbState';
import type { ThemeName } from './palette';

const THEMES: readonly ThemeName[] = ['light', 'dark'];

function muted(theme: ThemeName) {
  return deriveOrbPresentation({ ...INITIAL_STATE, phase: 'listening', overlay: 'muted', micActive: false }, theme);
}

describe('accessible name agrees with the enabled actions', () => {
  it('advertises only actions the resolved state enables', () => {
    // Each phrase is the only way the corresponding action can enter the
    // accessible name; if a phrase appears, the action must be available.
    const phrases: ReadonlyArray<readonly [OrbAction, string]> = [
      ['start', 'Start voice conversation'],
      ['cancel', 'Cancel connection'],
      ['mute', 'Mute microphone'],
      ['unmute', 'Unmute microphone'],
      ['interrupt', 'Interrupt Sarjy'],
      ['end', 'End conversation'],
      ['retry', 'Retry connection'],
      ['reconnect', 'Reconnect'],
    ];
    for (const theme of THEMES) {
      for (const phase of VOICE_PHASES) {
        for (const overlay of ORB_OVERLAYS) {
          const presentation = deriveOrbPresentation({ ...INITIAL_STATE, phase, overlay }, theme);
          const name = presentation.ariaLabel.en;
          for (const [action, phrase] of phrases) {
            if (!name.includes(phrase)) continue;
            expect(presentation.enabledActions, `${phase}/${overlay} advertises ${action}`).toContain(action);
          }
        }
      }
    }
  });

  it('offers unmute, never mute, when the microphone is muted', () => {
    for (const theme of THEMES) {
      const presentation = muted(theme);
      expect(presentation.enabledActions).not.toContain('mute');
      expect(presentation.ariaLabel.en).toContain('Unmute microphone');
      expect(presentation.ariaLabel.en).not.toContain('Mute microphone');
      expect(presentation.ariaLabel.en).toBe('Microphone muted. Listening. Unmute microphone');
    }
  });
});
