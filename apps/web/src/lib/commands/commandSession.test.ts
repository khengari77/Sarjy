import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  saveCaptionSelection: vi.fn(),
  saveMemoryMode: vi.fn(),
  saveSpeakingSpeed: vi.fn(),
}));

vi.mock('../chat/session', () => mocks);

import { createCommandSession } from './commandSession.svelte';

const snapshot = (memory_mode: 'off' | 'ask' | 'auto', revision: number) => ({
  speaking_speed: 1,
  captions_visible: true,
  approved_voice_id: null,
  response_preference: 'automatic',
  memory_mode,
  revision,
});

describe('memory mode control', () => {
  beforeEach(() => {
    mocks.saveCaptionSelection.mockReset();
    mocks.saveMemoryMode.mockReset();
    mocks.saveSpeakingSpeed.mockReset();
  });

  it('writes the mode through the settings path and applies the server snapshot', async () => {
    mocks.saveMemoryMode.mockResolvedValue({
      kind: 'SetMemoryMode',
      settings: snapshot('off', 1),
      changed: true,
    });
    const session = createCommandSession();

    expect(await session.setMemoryMode('off')).toBe(true);

    expect(mocks.saveMemoryMode).toHaveBeenCalledWith('off', 0);
    expect(session.state.settings.memory_mode).toBe('off');
    expect(session.state.settings.revision).toBe(1);
    expect(session.state.last).toMatchObject({ kind: 'executed', action: 'SetMemoryMode', changed: true });
    expect(session.state.trace).toEqual(['SetMemoryMode']);
  });

  it('a rejected write leaves the previous mode and revision in place', async () => {
    mocks.saveMemoryMode.mockRejectedValue(new Error('SETTINGS_REVISION_CONFLICT'));
    const session = createCommandSession();

    expect(await session.setMemoryMode('ask')).toBe(false);

    expect(session.state.settings.memory_mode).toBe('auto');
    expect(session.state.settings.revision).toBe(0);
    expect(session.state.last).toMatchObject({ kind: 'failed', action: 'SetMemoryMode' });
    expect(session.state.trace).toEqual([]);
  });
});
