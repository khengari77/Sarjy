import { afterEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  request: vi.fn(),
  fetchConversation: vi.fn(),
  createConversation: vi.fn(),
  deleteConversation: vi.fn(),
}));

vi.mock('../api', () => ({ request: mocks.request }));
vi.mock('../chat/session', () => ({
  fetchConversation: mocks.fetchConversation,
  createConversation: mocks.createConversation,
  deleteConversation: mocks.deleteConversation,
}));

const { createConversationStore, initialConversationState } = await import('./conversations');
const stores: Array<ReturnType<typeof createConversationStore>> = [];

function storeFor(state: ReturnType<typeof initialConversationState>) {
  const store = createConversationStore(state, { getLive: () => false, endVoice: async () => {} });
  stores.push(store);
  return store;
}

afterEach(() => {
  for (const store of stores.splice(0)) store.dispose();
  vi.clearAllMocks();
  vi.useRealTimers();
});

it('refreshes the conversation list so a generated title appears', async () => {
  const state = initialConversationState();
  state.current = 'c1';
  state.conversations = [{ id: 'c1', title: 'New conversation', revision: 0 }];
  mocks.fetchConversation.mockResolvedValue({
    turns: [{ id: 't1', role: 'assistant', text: 'Welcome', status: 'completed' }],
    toolCalls: [],
  });
  mocks.request.mockResolvedValue([{ id: 'c1', title: 'Trip planning', revision: 0 }]);

  const store = storeFor(state);
  await store.refresh();

  expect(mocks.request).toHaveBeenCalledWith('conversations');
  expect(state.conversations[0].title).toBe('Trip planning');
  expect(state.turns).toHaveLength(1);
});

it('does not refetch the list once the conversation has a real title', async () => {
  const state = initialConversationState();
  state.current = 'c1';
  state.conversations = [{ id: 'c1', title: 'Trip planning', revision: 0 }];
  mocks.fetchConversation.mockResolvedValue({ turns: [], toolCalls: [] });

  const store = storeFor(state);
  await store.refresh();

  expect(mocks.request).not.toHaveBeenCalled();
});

it('ignores a stale refresh after the selection moved on', async () => {
  const state = initialConversationState();
  state.current = 'c1';
  let resolveHistory: (value: unknown) => void = () => {};
  let resolveList: (value: unknown) => void = () => {};
  mocks.fetchConversation.mockReturnValue(new Promise((resolve) => (resolveHistory = resolve)));
  mocks.request.mockReturnValue(new Promise((resolve) => (resolveList = resolve)));

  const store = storeFor(state);
  const pending = store.refresh();
  state.current = 'c2';
  resolveHistory({ turns: [{ id: 'stale' }], toolCalls: [] });
  resolveList([{ id: 'c1', title: 'Stale title', revision: 0 }]);
  await pending;

  expect(state.conversations).toEqual([]);
  expect(state.turns).toEqual([]);
});

it('removes a deleted chat and clears the selection when it was current', async () => {
  const state = initialConversationState();
  state.current = 'c1';
  state.conversations = [
    { id: 'c1', title: 'Trip planning', revision: 0 },
    { id: 'c2', title: 'Work ideas', revision: 0 },
  ];
  state.turns = [{ id: 't1', role: 'user', text: 'Hello', status: 'completed' }];
  state.toolCalls = [{ id: 'tool-1', name: 'weather', result: {} }];
  mocks.deleteConversation.mockResolvedValue(undefined);
  mocks.request.mockResolvedValue([{ id: 'c2', title: 'Work ideas', revision: 0 }]);

  const store = storeFor(state);
  await store.remove('c1');

  expect(mocks.deleteConversation).toHaveBeenCalledWith('c1');
  expect(mocks.request).toHaveBeenCalledWith('conversations');
  expect(state.conversations).toEqual([{ id: 'c2', title: 'Work ideas', revision: 0 }]);
  expect(state.current).toBeNull();
  expect(state.turns).toEqual([]);
  expect(state.toolCalls).toEqual([]);
});

it('drops a list response that started before a delete', async () => {
  const state = initialConversationState();
  state.current = 'c1';
  state.conversations = [
    { id: 'c1', title: 'New conversation', revision: 0 },
    { id: 'c2', title: 'Work ideas', revision: 0 },
  ];
  let resolveHistory: (value: unknown) => void = () => {};
  let resolveList: (value: unknown) => void = () => {};
  mocks.fetchConversation.mockReturnValue(new Promise((resolve) => (resolveHistory = resolve)));
  mocks.request.mockReturnValueOnce(new Promise((resolve) => (resolveList = resolve)));
  mocks.request.mockResolvedValueOnce([{ id: 'c1', title: 'Trip planning', revision: 0 }]);
  mocks.deleteConversation.mockResolvedValue(undefined);

  const store = storeFor(state);
  const refreshing = store.refresh();
  await store.remove('c2');

  resolveHistory({ turns: [], toolCalls: [] });
  resolveList([
    { id: 'c1', title: 'Trip planning', revision: 0 },
    { id: 'c2', title: 'Work ideas', revision: 0 },
  ]);
  await refreshing;

  expect(state.conversations).toEqual([{ id: 'c1', title: 'Trip planning', revision: 0 }]);
});

it('does not report a failed list refresh as a delete failure', async () => {
  const state = initialConversationState();
  state.current = 'c1';
  state.conversations = [
    { id: 'c1', title: 'Trip planning', revision: 0 },
    { id: 'c2', title: 'Work ideas', revision: 0 },
  ];
  mocks.deleteConversation.mockResolvedValue(undefined);
  mocks.request.mockRejectedValueOnce(new Error('API_UNAVAILABLE'));

  const store = storeFor(state);

  await expect(store.remove('c2')).resolves.toBeUndefined();
  expect(state.conversations).toEqual([{ id: 'c1', title: 'Trip planning', revision: 0 }]);
});

it('keeps the current selection when a different chat is deleted', async () => {
  const state = initialConversationState();
  state.current = 'c1';
  state.conversations = [
    { id: 'c1', title: 'Trip planning', revision: 0 },
    { id: 'c2', title: 'Work ideas', revision: 0 },
  ];
  mocks.deleteConversation.mockResolvedValue(undefined);
  mocks.request.mockResolvedValue([{ id: 'c1', title: 'Trip planning', revision: 0 }]);

  const store = storeFor(state);
  await store.remove('c2');

  expect(state.current).toBe('c1');
  expect(state.conversations).toEqual([{ id: 'c1', title: 'Trip planning', revision: 0 }]);
});

it('keeps an in-flight list reload when a refresh does not fetch the list', async () => {
  const state = initialConversationState();
  state.current = 'c1';
  state.conversations = [{ id: 'c1', title: 'Trip planning', revision: 0 }];
  let resolveList: (value: unknown) => void = () => {};
  mocks.request.mockReturnValueOnce(new Promise((resolve) => (resolveList = resolve)));
  mocks.fetchConversation.mockResolvedValue({ turns: [], toolCalls: [] });

  const store = storeFor(state);
  const reloading = store.reload();
  await store.refresh(); // the title is set, so this fetches the chat only and must not supersede the reload
  resolveList([
    { id: 'c1', title: 'Trip planning', revision: 0 },
    { id: 'c2', title: 'Work ideas', revision: 0 },
  ]);
  await reloading;

  expect(state.conversations.map((chat) => chat.id)).toEqual(['c1', 'c2']);
});
