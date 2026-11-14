/** One reactive command session: the GUI and worker results share this state. */

import { browserStorage } from '../storage';
import { createCommandStore, initialCommandState, type SettingsSnapshot } from './commandStore';

export function persistSettings(settings: SettingsSnapshot) {
  const storage = browserStorage();
  if (!storage) return;
  storage.setItem('sarjy-captions', settings.captions_visible ? 'on' : 'off');
}

export function createCommandSession() {
  const state = $state(initialCommandState());
  const store = createCommandStore(state, { persist: persistSettings });
  return {
    state,
    apply: store.apply,
    hydrate: store.hydrate,
    setCaptions: store.setCaptions,
    openPanel: store.openPanel,
  };
}

export type CommandSession = ReturnType<typeof createCommandSession>;
