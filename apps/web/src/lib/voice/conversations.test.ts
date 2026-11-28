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

afterEach(() => {
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

  const store = createConversationStore(state, { getLive: () => false, endVoice: async () => {} });
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

  const store = createConversationStore(state, { getLive: () => false, endVoice: async () => {} });
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

  const store = createConversationStore(state, { getLive: () => false, endVoice: async () => {} });
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

  const store = createConversationStore(state, { getLive: () => false, endVoice: async () => {} });
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

  const store = createConversationStore(state, { getLive: () => false, endVoice: async () => {} });
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

  const store = createConversationStore(state, { getLive: () => false, endVoice: async () => {} });

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

  const store = createConversationStore(state, { getLive: () => false, endVoice: async () => {} });
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

  const store = createConversationStore(state, { getLive: () => false, endVoice: async () => {} });
  const reloading = store.reload();
  await store.refresh(); // the title is set, so this fetches the chat only and must not supersede the reload
  resolveList([
    { id: 'c1', title: 'Trip planning', revision: 0 },
    { id: 'c2', title: 'Work ideas', revision: 0 },
  ]);
  await reloading;

  expect(state.conversations.map((chat) => chat.id)).toEqual(['c1', 'c2']);
});

it('retries an empty failed list after 1.5 s then 4 s, then stops', async () => {
  vi.useFakeTimers();
  mocks.request.mockRejectedValue(new Error('API_UNAVAILABLE'));
  const state = initialConversationState();
  const store = createConversationStore(state, { getLive: () => false, endVoice: async () => {} });

  await store.reload();
  expect(state.listError).toBe('API_UNAVAILABLE');
  expect(state.listRetrying).toBe(true);
  expect(mocks.request).toHaveBeenCalledTimes(1);

  await vi.advanceTimersByTimeAsync(1_499);
  expect(mocks.request).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(1);
  expect(mocks.request).toHaveBeenCalledTimes(2);
  expect(state.listRetrying).toBe(true);

  await vi.advanceTimersByTimeAsync(3_999);
  expect(mocks.request).toHaveBeenCalledTimes(2);
  await vi.advanceTimersByTimeAsync(1);
  expect(mocks.request).toHaveBeenCalledTimes(3);
  expect(state.listRetrying).toBe(false);

  await vi.advanceTimersByTimeAsync(10_000);
  expect(mocks.request).toHaveBeenCalledTimes(3);
});

it('stops the automatic retry once the list loads', async () => {
  vi.useFakeTimers();
  mocks.request.mockRejectedValueOnce(new Error('API_UNAVAILABLE')).mockResolvedValueOnce([]);
  const state = initialConversationState();
  const store = createConversationStore(state, { getLive: () => false, endVoice: async () => {} });

  await store.reload();
  expect(state.listRetrying).toBe(true);
  await vi.advanceTimersByTimeAsync(1_500);
  expect(mocks.request).toHaveBeenCalledTimes(2);
  expect(state.listError).toBe('');
  expect(state.listRetrying).toBe(false);

  await vi.advanceTimersByTimeAsync(10_000);
  expect(mocks.request).toHaveBeenCalledTimes(2);
});

it('stops the automatic retry when the user clicks Retry', async () => {
  vi.useFakeTimers();
  mocks.request.mockRejectedValue(new Error('API_UNAVAILABLE'));
  const state = initialConversationState();
  const store = createConversationStore(state, { getLive: () => false, endVoice: async () => {} });

  await store.reload();
  await store.retry();
  expect(mocks.request).toHaveBeenCalledTimes(2);
  expect(state.listRetrying).toBe(false);

  await vi.advanceTimersByTimeAsync(10_000);
  expect(mocks.request).toHaveBeenCalledTimes(2);
});

it('stops the automatic retry on dispose', async () => {
  vi.useFakeTimers();
  mocks.request.mockRejectedValue(new Error('API_UNAVAILABLE'));
  const state = initialConversationState();
  const store = createConversationStore(state, { getLive: () => false, endVoice: async () => {} });

  await store.reload();
  expect(state.listRetrying).toBe(true);
  store.dispose();
  expect(state.listRetrying).toBe(false);

  await vi.advanceTimersByTimeAsync(10_000);
  expect(mocks.request).toHaveBeenCalledTimes(1);
});
