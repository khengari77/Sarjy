import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ save: vi.fn(), fetch: vi.fn() }));
vi.mock('../chat/session', () => ({
  saveVoiceSelection: mocks.save,
  fetchSettings: mocks.fetch,
  mergeVoices: (current: unknown[], incoming: unknown[]) => [...current, ...incoming],
}));

import { createCatalogStore, initialCatalogState } from './catalog';

const settings = (voiceId: string | null, revision: number) => ({
  speaking_speed: 1,
  captions_visible: true,
  approved_voice_id: voiceId,
  response_preference: 'automatic',
  revision,
});

describe('catalog store revision recovery', () => {
  beforeEach(() => {
    mocks.save.mockReset();
    mocks.fetch.mockReset();
  });

  it('refetches settings and retries once after a revision conflict', async () => {
    const state = initialCatalogState('alpha');
    const store = createCatalogStore(state);
    state.voiceId = 'beta';
    mocks.save
      .mockRejectedValueOnce(new Error('SETTINGS_REVISION_CONFLICT'))
      .mockResolvedValueOnce(settings('beta', 6));
    mocks.fetch.mockResolvedValueOnce(settings('other-tab', 5));

    await store.remember();

    expect(mocks.fetch).toHaveBeenCalledTimes(1);
    expect(mocks.save.mock.calls).toEqual([
      ['beta', 0],
      ['beta', 5],
    ]);
    expect(state.voiceId).toBe('beta');
    expect(state.savedVoiceId).toBe('beta');
    expect(state.revision).toBe(6);
    expect(state.settingsError).toBe('');
  });

  it('surfaces a scoped error when the retry still conflicts', async () => {
    const state = initialCatalogState('alpha');
    const store = createCatalogStore(state);
    state.voiceId = 'beta';
    mocks.save
      .mockRejectedValueOnce(new Error('SETTINGS_REVISION_CONFLICT'))
      .mockRejectedValueOnce(new Error('SETTINGS_REVISION_CONFLICT'));
    mocks.fetch.mockResolvedValueOnce(settings('other-tab', 5));

    await store.remember();

    expect(state.voiceId).toBe('alpha');
    expect(state.settingsError).toBe('SETTINGS_REVISION_CONFLICT');
  });
});
