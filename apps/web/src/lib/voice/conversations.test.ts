import { afterEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  request: vi.fn(),
  fetchConversation: vi.fn(),
  createConversation: vi.fn(),
}));

vi.mock('../api', () => ({ request: mocks.request }));
vi.mock('../chat/session', () => ({
  fetchConversation: mocks.fetchConversation,
  createConversation: mocks.createConversation,
}));

const { createConversationStore, initialConversationState } = await import('./conversations');

afterEach(() => vi.clearAllMocks());

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
