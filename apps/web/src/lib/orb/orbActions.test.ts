import { describe, expect, it, vi } from 'vitest';
import { ACTION_ARIA } from './labels';
import { activateOrb, type OrbActionSurface } from './orbActions';
import { INITIAL_STATE, ORB_OVERLAYS, VOICE_PHASES, deriveOrbPresentation, type OrbAction } from './orbState';
import type { ThemeName } from './palette';

const THEMES: readonly ThemeName[] = ['light', 'dark'];

const METHOD: Record<OrbAction, keyof OrbActionSurface> = {
  start: 'start',
  cancel: 'cancel',
  mute: 'mute',
  unmute: 'mute',
  interrupt: 'interrupt',
  retry: 'retry',
  reconnect: 'reconnect',
  end: 'end',
};

function surface() {
  return {
    start: vi.fn(),
    cancel: vi.fn(),
    mute: vi.fn(),
    interrupt: vi.fn(),
    retry: vi.fn(),
    reconnect: vi.fn(),
    end: vi.fn(),
  } satisfies OrbActionSurface;
}

describe('activating the orb performs the advertised action', () => {
  it('dispatches exactly one connection method per reachable state', () => {
    for (const theme of THEMES) {
      for (const phase of VOICE_PHASES) {
        for (const overlay of ORB_OVERLAYS) {
          const presentation = deriveOrbPresentation({ ...INITIAL_STATE, phase, overlay }, theme);
          const handlers = surface();
          activateOrb(presentation, handlers);
          const called = Object.entries(handlers).filter(([, spy]) => spy.mock.calls.length > 0);
          const id = `${theme}/${phase}/${overlay}`;

          if (!presentation.primaryAction) {
            expect(called, id).toHaveLength(0);
            continue;
          }
          expect(presentation.enabledActions, id).toContain(presentation.primaryAction);
          expect(
            called.map(([name]) => name),
            id,
          ).toEqual([METHOD[presentation.primaryAction]]);
        }
      }
    }
  });

  it('keeps the accessible name on the same action the button performs', () => {
    for (const theme of THEMES) {
      for (const phase of VOICE_PHASES) {
        for (const overlay of ORB_OVERLAYS) {
          const presentation = deriveOrbPresentation({ ...INITIAL_STATE, phase, overlay }, theme);
          const id = `${theme}/${phase}/${overlay}`;
          if (!presentation.primaryAction) continue;
          expect(presentation.ariaLabel.en, id).toContain(ACTION_ARIA[presentation.primaryAction].en);
        }
      }
    }
  });
});
