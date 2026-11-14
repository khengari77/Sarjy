/** Browser wiring for the one shared command store (GUI + worker results). */

import { request } from '../api';
import { browserStorage } from '../storage';
import {
  createCommandStore,
  resultFromUiResponse,
  type ActionPayload,
  type CommandResult,
  type CommandState,
  type SettingsSnapshot,
} from './commandStore';

export function persistSettings(settings: SettingsSnapshot) {
  const storage = browserStorage();
  if (!storage) return;
  storage.setItem('sarjy-captions', settings.captions_visible ? 'on' : 'off');
  storage.setItem('sarjy-speaking-speed', String(settings.speaking_speed));
}

export function createBrowserCommands(
  state: CommandState,
  getConversationId: () => string | null,
  onApplied?: (result: CommandResult) => void,
) {
  const store = createCommandStore(
    state,
    {
      send: async (action: ActionPayload, requestId: string, expectedRevision: number) => {
        const conversationId = getConversationId();
        if (!conversationId) throw new Error('CONVERSATION_REQUIRED');
        const body = await request('actions', {
          conversation_id: conversationId,
          request_id: requestId,
          expected_revision: expectedRevision,
          source: 'ui',
          action,
        });
        const result = resultFromUiResponse(requestId, body);
        if (!result) throw new Error('ACTION_RESULT_INVALID');
        return result;
      },
    },
    { persist: persistSettings },
  );

  function apply(result: CommandResult): boolean {
    if (!store.apply(result)) return false;
    onApplied?.(result);
    return true;
  }

  return {
    store,
    apply,
    toggleCaptions: () => void store.setCaptions(!state.settings.captions_visible),
    setSpeed: (speed: number) => void store.setSpeed(speed),
  };
}
