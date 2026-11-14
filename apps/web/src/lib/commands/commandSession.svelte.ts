/** One reactive command session: the GUI and worker results share this state. */

import { saveCaptionSelection } from '../chat/session';
import type { CatalogState } from '../voice/catalog';
import { createCommandStore, initialCommandState, type CommandResult } from './commandStore';

export function createCommandSession(catalog?: CatalogState, panels?: { history: boolean; memory_explorer: boolean }) {
  const state = $state(initialCommandState());
  if (panels) state.panels = panels;
  const store = createCommandStore(state);

  function apply(result: CommandResult): boolean {
    if (!store.apply(result)) return false;
    if (catalog && result.settings) {
      catalog.revision = result.settings.revision;
      catalog.savedVoiceId = result.settings.approved_voice_id ?? '';
      if (result.action === 'SelectApprovedVoice') catalog.voiceId = catalog.savedVoiceId;
    }
    return true;
  }

  /**
   * Captions are server-owned: this sends the canonical settings action and
   * applies the authoritative snapshot. A rejected write changes nothing.
   */
  async function setCaptions(visible: boolean): Promise<boolean> {
    const revision = state.settings.revision;
    const requestId = `captions:${revision}:${visible ? 'on' : 'off'}`;
    try {
      const result = await saveCaptionSelection(visible, revision);
      return apply({
        request_id: requestId,
        kind: result.changed ? 'executed' : 'unchanged',
        action: 'SetCaptionsVisible',
        settings: result.settings,
        navigation: null,
        changed: result.changed,
        spoken: result.settings.captions_visible
          ? result.changed
            ? 'Captions shown.'
            : 'Captions are already on.'
          : result.changed
            ? 'Captions hidden.'
            : 'Captions are already hidden.',
        detail: null,
      });
    } catch (error) {
      store.fail('SetCaptionsVisible', requestId, error instanceof Error ? error.message : 'SETTING_REJECTED');
      return false;
    }
  }

  return {
    state,
    apply,
    hydrate: store.hydrate,
    setCaptions,
    openPanel: store.openPanel,
  };
}

export type CommandSession = ReturnType<typeof createCommandSession>;
