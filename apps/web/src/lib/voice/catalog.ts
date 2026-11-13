import { request, type UserSettings, type VoiceCatalog, type VoiceOption } from '../api';
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

export function createCatalogStore(state: CatalogState, deps: { getConversationId(): string | null }) {
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
  }

  /** Persist the picker choice server-side; revert to the saved value on rejection. */
  async function remember() {
    if (state.saving) return;
    const conversationId = deps.getConversationId();
    const next = state.voiceId;
    if (!conversationId || next === state.savedVoiceId) return;
    const previous = state.savedVoiceId;
    state.saving = true;
    state.settingsError = '';
    try {
      applySettings(await saveVoiceSelection(conversationId, next, state.revision));
    } catch (error) {
      state.voiceId = previous;
      state.settingsError = error instanceof Error ? error.message : 'VOICE_SETTING_REJECTED';
    } finally {
      state.saving = false;
    }
  }

  return { load, loadSettings, remember };
}
