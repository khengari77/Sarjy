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

it('retries an empty failed list after 1.5 s then 4 s, then stops', async () => {
  vi.useFakeTimers();
  mocks.request.mockRejectedValue(new Error('API_UNAVAILABLE'));
  const state = initialConversationState();
  const store = storeFor(state);

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
  const store = storeFor(state);

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
  const store = storeFor(state);

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
  const store = storeFor(state);

  await store.reload();
  expect(state.listRetrying).toBe(true);
  store.dispose();
  expect(state.listRetrying).toBe(false);

  await vi.advanceTimersByTimeAsync(10_000);
  expect(mocks.request).toHaveBeenCalledTimes(1);
});

it('does not auto-retry while a stale list is on screen', async () => {
  vi.useFakeTimers();
  mocks.request.mockRejectedValue(new Error('API_UNAVAILABLE'));
  const state = initialConversationState();
  state.conversations = [{ id: 'c1', title: 'Trip planning', revision: 0 }];
  const store = storeFor(state);

  await store.reload();
  expect(state.listError).toBe('API_UNAVAILABLE');
  expect(state.listRetrying).toBe(false);

  await vi.advanceTimersByTimeAsync(10_000);
  expect(mocks.request).toHaveBeenCalledTimes(1);
});

it('restarts the automatic retry schedule from 1.5 s after reload', async () => {
  vi.useFakeTimers();
  mocks.request.mockRejectedValue(new Error('API_UNAVAILABLE'));
  const state = initialConversationState();
  const store = storeFor(state);

  await store.reload();
  await vi.advanceTimersByTimeAsync(1_000);
  await store.reload();
  expect(mocks.request).toHaveBeenCalledTimes(2);

  await vi.advanceTimersByTimeAsync(1_499);
  expect(mocks.request).toHaveBeenCalledTimes(2);
  await vi.advanceTimersByTimeAsync(1);
  expect(mocks.request).toHaveBeenCalledTimes(3);
});

it.each(['ACCOUNT_SESSION_REQUIRED', 'HTTP_401', 'HTTP_403'])(
  'does not auto-retry an auth failure (%s)',
  async (code) => {
    vi.useFakeTimers();
    mocks.request.mockRejectedValue(new Error(code));
    const state = initialConversationState();
    const store = storeFor(state);

    await store.reload();
    expect(state.listError).toBe(code);
    expect(state.listRetrying).toBe(false);

    await vi.advanceTimersByTimeAsync(10_000);
    expect(mocks.request).toHaveBeenCalledTimes(1);
  },
);

it('does not write state from an in-flight load after dispose', async () => {
  let resolveList: (value: unknown) => void = () => {};
  mocks.request.mockReturnValueOnce(new Promise((resolve) => (resolveList = resolve)));
  const state = initialConversationState();
  const loading = state.loading;
  const store = storeFor(state);

  const pending = store.reload();
  store.dispose();
  resolveList([{ id: 'c1', title: 'Trip planning', revision: 0 }]);
  await pending;

  expect(state.conversations).toEqual([]);
  expect(state.listError).toBe('');
  expect(state.loading).toBe(loading);
});
