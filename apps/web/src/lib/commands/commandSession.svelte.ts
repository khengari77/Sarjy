/** One reactive command session: the GUI and worker results share this state. */

import { createBrowserCommands } from './browserCommands';
import { initialCommandState, type CommandResult } from './commandStore';

export function createCommandSession(
  getConversationId: () => string | null,
  onApplied?: (result: CommandResult) => void,
) {
  const state = $state(initialCommandState());
  const commands = createBrowserCommands(state, getConversationId, onApplied);
  return {
    state,
    apply: commands.apply,
    hydrate: commands.store.hydrate,
    toggleCaptions: commands.toggleCaptions,
    setSpeed: commands.setSpeed,
  };
}

export type CommandSession = ReturnType<typeof createCommandSession>;
