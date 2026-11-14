import { request, type UserSettings, type VoiceCatalog, type VoiceOption } from '../api';
import type { CommandResult } from '../commands/commandStore';
import { fetchSettings, mergeVoices, saveVoiceSelection } from '../chat/session';

export interface CatalogState {
  /** Voice selected for the next call/session. */
  voiceId: string;
  /** Last voice confirmed saved by the server. */
  savedVoiceId: string;
  revision: number;
  voices: VoiceOption[];
  search: string;
  loadedSearch: string;
  nextPage: string | null;
  loading: boolean;
  saving: boolean;
  error: string;
  settingsError: string;
}

export function initialCatalogState(voiceId = ''): CatalogState {
  return {
    voiceId,
    savedVoiceId: voiceId,
    revision: 0,
    voices: [],
    search: '',
    loadedSearch: '',
    nextPage: null,
    loading: false,
    saving: false,
    error: '',
    settingsError: '',
  };
}

export function createCatalogStore(
  state: CatalogState,
  options: { onSettings?(settings: UserSettings): void; onVoiceSaved?(result: CommandResult): void } = {},
) {
  async function load(more = false) {
    if (state.loading) return;
    state.loading = true;
    state.error = '';
    try {
      const search = more ? state.loadedSearch : state.search;
      const query = new URLSearchParams({ search });
      if (more && state.nextPage) query.set('next_page_token', state.nextPage);
      const result = await request<VoiceCatalog>('voices?' + query);
      state.voices = more ? mergeVoices(state.voices, result.voices) : result.voices;
      state.nextPage = result.next_page_token ?? null;
      state.loadedSearch = search;
    } catch (error) {
      state.error = error instanceof Error ? error.message : 'VOICE_CATALOG_UNAVAILABLE';
    } finally {
      state.loading = false;
    }
  }

  /** Load the authoritative server settings. Never fall back to localStorage. */
  async function loadSettings() {
    try {
      applySettings(await fetchSettings());
    } catch {
      // The server still resolves its configured default when a call starts.
    }
  }

  function applySettings(settings: UserSettings) {
    state.revision = settings.revision;
    state.savedVoiceId = settings.approved_voice_id ?? '';
    state.voiceId = state.savedVoiceId;
    state.settingsError = '';
    options.onSettings?.(settings);
  }

  /** Persist the picker choice owner-side; revert to the saved value on rejection. */
  async function remember() {
    if (state.saving) return;
    const next = state.voiceId;
    if (next === state.savedVoiceId) return;
    const previous = state.savedVoiceId;
    state.saving = true;
    state.settingsError = '';
    try {
      await save(next);
    } catch (error) {
      if (error instanceof Error && error.message === 'SETTINGS_REVISION_CONFLICT') {
        // Another tab committed first: adopt its revision, then retry this
        // selection once instead of leaving the picker stale until reload.
        await loadSettings();
        try {
          await save(next);
        } catch (retryError) {
          state.voiceId = previous;
          state.settingsError = retryError instanceof Error ? retryError.message : 'VOICE_SETTING_REJECTED';
        }
      } else {
        state.voiceId = previous;
        state.settingsError = error instanceof Error ? error.message : 'VOICE_SETTING_REJECTED';
      }
    } finally {
      state.saving = false;
    }
  }

  async function save(voiceId: string) {
    const previous = state.savedVoiceId;
    const requestId = `voice:${state.revision}:${voiceId || 'default'}`;
    const saved = await saveVoiceSelection(voiceId, state.revision);
    applySettings(saved);
    const changed = saved.approved_voice_id !== previous;
    const name = state.voices.find((option) => option.id === voiceId)?.name || 'the selected voice';
    options.onVoiceSaved?.({
      request_id: requestId,
      kind: changed ? 'executed' : 'unchanged',
      action: 'SelectApprovedVoice',
      settings: saved,
      navigation: null,
      changed,
      spoken: changed ? `Voice set to ${name} for the next call.` : `${name} is already saved for the next call.`,
      detail: null,
    });
  }

  return { load, loadSettings, remember };
}
