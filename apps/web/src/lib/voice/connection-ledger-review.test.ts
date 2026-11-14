import { expect, it, vi } from 'vitest';
import { createVoiceConnection, initialVoiceState } from './connection';
const mocks = vi.hoisted(() => ({ start: vi.fn(), end: vi.fn() }));
vi.mock('../chat/session', () => ({ startVoiceSession: mocks.start, endVoiceSession: mocks.end }));
const a = { id: 'a', url: 'ws://fixture', token: 'fixture', expires_at: '2099-01-01' };
const b = { ...a, id: 'b' };
function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((r) => (resolve = r));
  return { promise, resolve };
}
function harness() {
  const state = initialVoiceState();
  state.live = a;
  state.muted = false;
  const runtime = {
    connect: vi.fn(async () => {}),
    disconnect: vi.fn(async () => {}),
    cancelConnect: vi.fn(async () => {}),
    startMicrophone: vi.fn(async () => {}),
    stopMicrophone: vi.fn(async () => {}),
    setMicrophoneEnabled: vi.fn(async () => {}),
    on: vi.fn(() => () => {}),
  };
  const connection = createVoiceConnection(state, {
    getRuntime: () => runtime as never,
    getConversationId: () => 'c',
    ensureConversation: async () => 'c',
    refresh: async () => {},
    getLanguage: () => 'en',
    getVoice: () => ({ id: 'voice', name: 'voice' }),
    meters: { teardownMeters: vi.fn(), setupMeters: vi.fn() } as never,
  });
  return { state, runtime, connection };
}
it('ends the replacement while an older session release is still pending', async () => {
  const hold = deferred();
  mocks.end.mockImplementationOnce(() => hold.promise).mockResolvedValue(undefined);
  mocks.start.mockResolvedValue(b);
  const { state, runtime, connection } = harness();
  const oldEnd = connection.end();
  try {
    await vi.waitFor(() => expect(mocks.end).toHaveBeenCalledWith(a));
    await connection.connect(false);
    expect(state.live?.id).toBe('b');
    await connection.end();
    expect(state.live).toBeNull();
    expect(runtime.disconnect).toHaveBeenCalledTimes(2);
  } finally {
    hold.resolve();
    await oldEnd;
    vi.clearAllMocks();
  }
});
it('processes the replacement disconnect while an older session release is pending', async () => {
  const hold = deferred();
  mocks.end.mockImplementationOnce(() => hold.promise).mockResolvedValue(undefined);
  mocks.start.mockResolvedValue(b);
  const { state, connection } = harness();
  const oldEnd = connection.end();
  try {
    await vi.waitFor(() => expect(mocks.end).toHaveBeenCalledWith(a));
    await connection.connect(false);
    state.muted = false;
    connection.handleEvent({ type: 'status', source: 'transport', text: 'disconnected', epoch: 1 });
    expect(state.connectionLost).toBe(true);
    expect(state.muted).toBe(true);
  } finally {
    hold.resolve();
    await oldEnd;
    vi.clearAllMocks();
  }
});
it('stops local media before waiting for the cancelled session backend release', async () => {
  const hold = deferred();
  mocks.end.mockImplementationOnce(() => hold.promise);
  const { runtime, connection } = harness();
  const cancel = connection.cancelConnect();
  try {
    await vi.waitFor(() => expect(mocks.end).toHaveBeenCalledWith(a));
    expect(runtime.cancelConnect).toHaveBeenCalledOnce();
  } finally {
    hold.resolve();
    await cancel;
    vi.clearAllMocks();
  }
});
