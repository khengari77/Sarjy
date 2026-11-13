import { describe, expect, it } from 'vitest';
import { voiceStatus, voiceStatusText } from './voice-state';

const voice = (id: string, name: string) => ({ id, name, languages: [] });

describe('voice status labels', () => {
  it('describes the server default when nothing is selected', () => {
    const state = { selected: null, session: null, confirmed: null, live: false };
    expect(voiceStatus(state)).toBe('default-next-call');
    expect(voiceStatusText(state, false)).toContain('default voice');
  });

  it('separates a next-call selection from a running call awaiting the worker ACK', () => {
    const state = {
      selected: voice('a', 'Alpha'),
      session: voice('b', 'Beta'),
      confirmed: null,
      live: true,
    };
    expect(voiceStatus(state)).toBe('selected-awaiting-confirmation');
    expect(voiceStatusText(state, false)).toContain('awaiting worker confirmation');
    expect(voiceStatusText(state, false)).not.toContain('Active voice');
  });

  it('only says Active for a worker-confirmed voice', () => {
    const state = {
      selected: voice('a', 'Alpha'),
      session: voice('a', 'Alpha'),
      confirmed: voice('a', 'Alpha'),
      live: true,
    };
    expect(voiceStatus(state)).toBe('confirmed-active');
    expect(voiceStatusText(state, false)).toBe('Active voice: Alpha');
  });
});
