import { request, type Conversation, type Turn } from '../api';
import { createConversation, fetchConversation, type ToolCall } from '../chat/session';

export interface ConversationState {
  conversations: Conversation[];
  current: string | null;
  turns: Turn[];
  toolCalls: ToolCall[];
  loading: boolean;
  listError: string;
}

export function initialConversationState(): ConversationState {
  return { conversations: [], current: null, turns: [], toolCalls: [], loading: true, listError: '' };
}

const UNTITLED_TITLE = 'New conversation';

export function createConversationStore(
  state: ConversationState,
  deps: { getLive(): boolean; endVoice(): Promise<void> },
) {
  let selectionEpoch = 0;

  async function refresh() {
    const id = state.current;
    const epoch = ++selectionEpoch;
    if (!id) return;
    // Titles are generated after a turn is durable. Keep polling the list only
    // while this conversation still carries the sentinel, then stop.
    const selected = state.conversations.find((chat) => chat.id === id);
    const needsTitle = !selected || selected.title === UNTITLED_TITLE;
    const [history, list] = await Promise.all([
      fetchConversation(id),
      needsTitle ? request<Conversation[]>('conversations') : Promise.resolve(null),
    ]);
    if (state.current === id && epoch === selectionEpoch) {
      state.turns = history.turns;
      state.toolCalls = history.toolCalls;
      if (list) state.conversations = list;
    }
  }

  async function reload() {
    state.listError = '';
    state.loading = true;
    try {
      state.conversations = await request<Conversation[]>('conversations');
    } finally {
      state.loading = false;
    }
  }

  async function createChat(): Promise<Conversation> {
    const chat = await createConversation();
    state.conversations = [chat, ...state.conversations];
    return chat;
  }

  async function ensureConversation(): Promise<string> {
    const chat = await createChat();
    state.current = chat.id;
    await refresh();
    return chat.id;
  }

  async function select(id: string) {
    if (deps.getLive()) await deps.endVoice();
    state.current = id;
    await refresh();
  }

  return { state, refresh, reload, createChat, ensureConversation, select };
}

export type ConversationStore = ReturnType<typeof createConversationStore>;
