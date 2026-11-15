import type { PendingIntent } from '../chat/intent';
import type { VoiceState } from './connection';
import type { ConversationStore } from './conversations';

export interface SelectionDeps {
  voice: VoiceState;
  getPending(): PendingIntent;
  setPending(intent: PendingIntent): void;
  run(work: () => Promise<void>): Promise<void>;
  cancelConnect(): Promise<void>;
  endVoice(): Promise<void>;
  getConversations(): ConversationStore;
}

export function createSelectionController(deps: SelectionDeps) {
  function requestSelect(id: string) {
    if (id === deps.getConversations().state.current) return;
    if (deps.voice.live) {
      deps.setPending({ type: 'switch', id });
      return;
    }
    if (deps.voice.connecting) {
      void deps.run(async () => {
        await deps.cancelConnect();
        await deps.getConversations().select(id);
      });
      return;
    }
    void deps.run(() => deps.getConversations().select(id));
  }

  async function confirmSwitch() {
    const intent = deps.getPending();
    deps.setPending(null);
    if (!intent) return;
    await deps.run(async () => {
      await deps.endVoice();
      const conversations = deps.getConversations();
      if (intent.type === 'create') {
        const chat = await conversations.createChat();
        await conversations.select(chat.id);
      } else {
        conversations.state.current = intent.id;
        await conversations.refresh();
      }
    });
  }

  async function create() {
    const conversations = deps.getConversations();
    if (deps.voice.live) {
      deps.setPending({ type: 'create' });
      return;
    }
    if (deps.voice.connecting) {
      await deps.run(async () => {
        await deps.cancelConnect();
        const chat = await conversations.createChat();
        await conversations.select(chat.id);
      });
      return;
    }
    await deps.run(async () => {
      const chat = await conversations.createChat();
      await conversations.select(chat.id);
    });
  }

  return { requestSelect, confirmSwitch, create };
}
