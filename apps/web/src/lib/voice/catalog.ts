import { request, type VoiceCatalog, type VoiceOption } from '../api';
import { mergeVoices } from '../chat/session';

export interface CatalogState {
  voiceId: string;
  voices: VoiceOption[];
  search: string;
  loadedSearch: string;
  nextPage: string | null;
  loading: boolean;
  error: string;
}

export function initialCatalogState(voiceId = ''): CatalogState {
  return { voiceId, voices: [], search: '', loadedSearch: '', nextPage: null, loading: false, error: '' };
}

export function createCatalogStore(state: CatalogState) {
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

  function remember() {
    localStorage.setItem('sarjy-voice-id', state.voiceId);
  }

  return { load, remember };
}
