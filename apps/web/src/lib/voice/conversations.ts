import { request, type Conversation, type Turn } from '../api';
import { createConversation, deleteConversation, fetchConversation, type ToolCall } from '../chat/session';
import { errorCode } from './errors';

export interface ConversationState {
  conversations: Conversation[];
  current: string | null;
  turns: Turn[];
  toolCalls: ToolCall[];
  loading: boolean;
  listError: string;
  /** True while an automatic list retry is scheduled after an empty-list error. */
  listRetrying: boolean;
}

export function initialConversationState(): ConversationState {
  return {
    conversations: [],
    current: null,
    turns: [],
    toolCalls: [],
    loading: true,
    listError: '',
    listRetrying: false,
  };
}

const UNTITLED_TITLE = 'New conversation';
const AUTO_RETRY_DELAYS_MS = [1_500, 4_000];
/** Codes that mean the session cannot recover by retrying the list. */
const AUTH_ERROR_CODES = new Set(['ACCOUNT_SESSION_REQUIRED', 'HTTP_401', 'HTTP_403']);

export function createConversationStore(
  state: ConversationState,
  deps: { getLive(): boolean; endVoice(): Promise<void> },
) {
  let selectionEpoch = 0;
  let listEpoch = 0;
  let autoRetryTimer: ReturnType<typeof setTimeout> | undefined;
  let autoRetryIndex = 0;
  let disposed = false;

  function cancelAutoRetry() {
    if (autoRetryTimer !== undefined) clearTimeout(autoRetryTimer);
    autoRetryTimer = undefined;
    state.listRetrying = false;
  }

  function scheduleAutoRetry() {
    if (disposed || autoRetryIndex >= AUTO_RETRY_DELAYS_MS.length) {
      state.listRetrying = false;
      return;
    }
    const delay = AUTO_RETRY_DELAYS_MS[autoRetryIndex];
    autoRetryIndex += 1;
    state.listRetrying = true;
    autoRetryTimer = setTimeout(() => {
      autoRetryTimer = undefined;
      void load(true);
    }, delay);
  }

  async function load(scheduleOnFailure: boolean) {
    if (disposed) return;
    cancelAutoRetry();
    const epoch = ++listEpoch;
    state.listError = '';
    state.loading = true;
    try {
      const list = await request<Conversation[]>('conversations');
      if (epoch !== listEpoch) return;
      state.conversations = list;
      autoRetryIndex = 0;
    } catch (error) {
      if (epoch !== listEpoch) return;
      const code = errorCode(error);
      state.listError = code;
      // Retry only an empty list on a transient failure: a stale list on screen
      // is better than a retry loop, and auth failures need a new session.
      if (scheduleOnFailure && !AUTH_ERROR_CODES.has(code) && state.conversations.length === 0) {
        scheduleAutoRetry();
      }
    } finally {
      if (epoch === listEpoch) state.loading = false;
    }
  }

  async function reload() {
    autoRetryIndex = 0;
    await load(true);
  }

  /** Manual Retry: cancel the automatic schedule and do not start a new one. */
  async function retry() {
    autoRetryIndex = 0;
    await load(false);
  }

  function dispose() {
    disposed = true;
    listEpoch += 1;
    cancelAutoRetry();
  }

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

  return { state, refresh, reload, retry, dispose, createChat, ensureConversation, select, remove };
}

export type ConversationStore = ReturnType<typeof createConversationStore>;
