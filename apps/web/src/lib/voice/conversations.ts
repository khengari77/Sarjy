import { request, type Conversation, type Turn } from '../api';
import { createConversation, deleteConversation, fetchConversation, type ToolCall } from '../chat/session';

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
  let listEpoch = 0;

  async function refresh() {
    const id = state.current;
    const epoch = ++selectionEpoch;
    if (!id) return;
    // Titles are generated after a turn is durable. Keep polling the list only
    // while this conversation still carries the sentinel, then stop.
    const selected = state.conversations.find((chat) => chat.id === id);
    const needsTitle = !selected || selected.title === UNTITLED_TITLE;
    // Only a refresh that fetches the list supersedes an earlier list request.
    const listEpochAtStart = needsTitle ? ++listEpoch : listEpoch;
    const [history, list] = await Promise.all([
      fetchConversation(id),
      needsTitle ? request<Conversation[]>('conversations') : Promise.resolve(null),
    ]);
    if (state.current === id && epoch === selectionEpoch) {
      state.turns = history.turns;
      state.toolCalls = history.toolCalls;
      if (list && listEpochAtStart === listEpoch) state.conversations = list;
    }
  }

  async function reload() {
    const epoch = ++listEpoch;
    state.listError = '';
    state.loading = true;
    try {
      const list = await request<Conversation[]>('conversations');
      if (epoch === listEpoch) state.conversations = list;
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

  async function remove(id: string) {
    // Invalidate any list request started before this deletion.
    listEpoch += 1;
    await deleteConversation(id);
    state.conversations = state.conversations.filter((chat) => chat.id !== id);
    if (state.current === id) {
      // Same empty selection the app starts with: no chat, no turns.
      state.current = null;
      state.turns = [];
      state.toolCalls = [];
    }
    try {
      await reload();
    } catch {
      // The delete is confirmed; a failed refresh must not be reported as a failed delete.
    }
  }

  return { state, refresh, reload, createChat, ensureConversation, select, remove };
}

export type ConversationStore = ReturnType<typeof createConversationStore>;
