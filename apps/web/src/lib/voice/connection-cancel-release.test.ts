import { afterEach, describe, expect, it, vi } from 'vitest';
import type { SessionConfig } from '../api';
import { initialVoiceState } from './connection';

const mocks = vi.hoisted(() => ({
  start: vi.fn(),
  end: vi.fn(async () => {}),
}));

vi.mock('../chat/session', () => ({
  startVoiceSession: mocks.start,
  endVoiceSession: mocks.end,
}));

interface FakeRoom {
  connectResolve: (() => void) | null;
  remoteParticipants: Map<string, { isAgent: boolean; attributes: Record<string, string> }>;
}

vi.mock('livekit-client', () => {
  const rooms: FakeRoom[] = [];
  class Room {
    listeners = new Map<string, Set<(...args: unknown[]) => void>>();
    remoteParticipants = new Map<string, { isAgent: boolean; attributes: Record<string, string> }>();
    localParticipant = {
      identity: 'user-1',
      setMicrophoneEnabled: async () => {},
      getTrackPublication: () => undefined,
    };
    canPlaybackAudio = true;
    connectResolve: (() => void) | null = null;
    constructor() {
      rooms.push(this as unknown as FakeRoom);
    }
    on(event: string, handler: (...args: unknown[]) => void) {
      if (!this.listeners.has(event)) this.listeners.set(event, new Set());
      this.listeners.get(event)!.add(handler);
    }
    emit(event: string, ...args: unknown[]) {
      for (const handler of this.listeners.get(event) ?? []) handler(...args);
    }
    connect() {
      return new Promise<void>((resolve) => {
        this.connectResolve = resolve;
      });
    }
    async startAudio() {}
    async disconnect() {}
    removeAllListeners() {
      this.listeners.clear();
    }
    registerTextStreamHandler() {}
  }
  (globalThis as unknown as { __connectionRooms: FakeRoom[] }).__connectionRooms = rooms;
  return {
    Room,
    RoomEvent: {
      TrackSubscribed: 'trackSubscribed',
      TrackUnsubscribed: 'trackUnsubscribed',
      AudioPlaybackStatusChanged: 'audioPlaybackStatusChanged',
      Reconnecting: 'reconnecting',
      Reconnected: 'reconnected',
      Disconnected: 'disconnected',
      ParticipantAttributesChanged: 'participantAttributesChanged',
      DataReceived: 'dataReceived',
      MediaDevicesError: 'mediaDevicesError',
      TranscriptionReceived: 'transcriptionReceived',
    },
    Track: { Kind: { Audio: 'audio' } },
  };
});

const { LiveKitVoiceRuntime } = await import('../audio/voice-runtime');
const { createVoiceConnection } = await import('./connection');
const rooms = () => (globalThis as unknown as { __connectionRooms: FakeRoom[] }).__connectionRooms;
const config: SessionConfig = {
  id: 'sess-1',
  url: 'ws://fake.invalid',
  token: 'token',
  expires_at: new Date(Date.now() + 60000).toISOString(),
};

function meters() {
  return {
    startMicMeter: vi.fn(),
    stopMicMeter: vi.fn(),
    startOutputMeter: vi.fn(),
    stopOutputMeter: vi.fn(),
    setupMeters: vi.fn(),
    teardownMeters: vi.fn(),
  };
}

function harness() {
  const state = initialVoiceState();
  const runtime = new LiveKitVoiceRuntime();
  const connection = createVoiceConnection(state, {
    ensureConversation: async () => 'c1',
    getConversationId: () => 'c1',
    refresh: async () => {},
    getRuntime: () => runtime,
    getLanguage: () => 'en',
    getVoice: () => ({ id: 'v1', name: 'Voice' }),
    meters: meters(),
  });
  return { state, runtime, connection };
}

async function liveConnection() {
  mocks.start.mockResolvedValueOnce(config);
  const harnessed = harness();
  const start = harnessed.connection.connect(false);
  await vi.advanceTimersByTimeAsync(1);
  const room = rooms().at(-1)!;
  room.remoteParticipants.set('agent', { isAgent: true, attributes: { 'lk.agent.state': 'listening' } });
  room.connectResolve!();
  await vi.advanceTimersByTimeAsync(100);
  await start;
  return { ...harnessed, room };
}

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  rooms().length = 0;
});

describe('cancellation after connect resolved', () => {
  it('releases the live session instead of abandoning it while tearing down', async () => {
    vi.useFakeTimers();
    const { state, connection } = await liveConnection();
    expect(state.live).toEqual(config);
    expect(state.connecting).toBe(false);

    await connection.cancelConnect();

    expect(mocks.end).toHaveBeenCalledExactlyOnceWith(config);
    expect(state.live).toBeNull();
    expect(state.voiceErrorCode).toBe('');
  });
});

describe('release results that overlap a replacement', () => {
  it('keeps a replacement session that connects during a failed cancel release', async () => {
    vi.useFakeTimers();
    const second: SessionConfig = { ...config, id: 'sess-2' };
    const { state, connection, runtime } = await liveConnection();
    mocks.start.mockResolvedValueOnce(second);
    let failRelease!: (error: Error) => void;
    mocks.end.mockImplementationOnce(
      () =>
        new Promise<void>((_resolve, reject) => {
          failRelease = reject;
        }),
    );

    const cancelling = connection.cancelConnect();
    await vi.advanceTimersByTimeAsync(0);
    expect(state.live).toBeNull();

    const replacement = connection.connect(false);
    await vi.advanceTimersByTimeAsync(1);
    const room = rooms().at(-1)!;
    room.remoteParticipants.set('agent', { isAgent: true, attributes: { 'lk.agent.state': 'listening' } });
    room.connectResolve!();
    await vi.advanceTimersByTimeAsync(100);
    await replacement;
    expect(state.live).toEqual(second);
    const epoch = runtime.epoch;

    failRelease(new Error('503'));
    await vi.advanceTimersByTimeAsync(0);
    await cancelling;

    expect(state.live).toEqual(second);
    expect(state.voiceErrorCode).toBe('');
    expect(runtime.epoch).toBe(epoch);
  });
});

describe('reconnect after a failed release', () => {
  it('does not unmute a dead session or claim a new connection', async () => {
    vi.useFakeTimers();
    const { state, connection } = await liveConnection();
    mocks.end.mockRejectedValueOnce(new Error('503'));

    await connection.reconnect();

    expect(mocks.start).toHaveBeenCalledOnce();
    expect(state.live).toEqual(config);
    expect(state.muted).toBe(true);
    expect(state.voiceErrorCode).toBe('SESSION_RELEASE_FAILED');
  });
});

describe('cancel with a stalled local transport', () => {
  it('issues the backend release while the local cancel is still running', async () => {
    vi.useFakeTimers();
    const { state, connection, runtime } = await liveConnection();
    const order: string[] = [];
    let finishLocal!: () => void;
    runtime.cancelConnect = () => {
      order.push('local-cancel-started');
      return new Promise<void>((resolve) => {
        finishLocal = resolve;
      });
    };
    mocks.end.mockImplementationOnce(async () => {
      order.push('release-issued');
    });

    const cancelling = connection.cancelConnect();
    await vi.advanceTimersByTimeAsync(0);

    // The release is issued after local teardown starts and without waiting
    // for it to settle; state.live is already cleared.
    expect(order).toEqual(['local-cancel-started', 'release-issued']);
    expect(state.live).toBeNull();

    finishLocal();
    await vi.advanceTimersByTimeAsync(0);
    await cancelling;
    expect(state.voiceErrorCode).toBe('');
  });

  it('releases the backend session even when the local cancel never settles', async () => {
    vi.useFakeTimers();
    const { state, connection, runtime } = await liveConnection();
    runtime.cancelConnect = () => new Promise<void>(() => {});

    void connection.cancelConnect();
    await vi.advanceTimersByTimeAsync(0);

    // The local teardown can hang forever; the admitted session is still released.
    expect(mocks.end).toHaveBeenCalledWith(config);
    expect(state.live).toBeNull();
  });
});

describe('end after a replacement connected', () => {
  it('keeps the replacement when the old session release fails', async () => {
    vi.useFakeTimers();
    const second: SessionConfig = { ...config, id: 'sess-2' };
    const { state, connection } = await liveConnection();
    mocks.start.mockResolvedValueOnce(second);
    let failRelease!: (error: Error) => void;
    mocks.end.mockImplementationOnce(
      () =>
        new Promise<void>((_resolve, reject) => {
          failRelease = reject;
        }),
    );

    const ending = connection.end();
    await vi.advanceTimersByTimeAsync(0);
    expect(state.live).toBeNull();

    const replacement = connection.connect(false);
    await vi.advanceTimersByTimeAsync(1);
    const room = rooms().at(-1)!;
    room.remoteParticipants.set('agent', { isAgent: true, attributes: { 'lk.agent.state': 'listening' } });
    room.connectResolve!();
    await vi.advanceTimersByTimeAsync(100);
    await replacement;
    expect(state.live).toEqual(second);

    failRelease(new Error('503'));
    await vi.advanceTimersByTimeAsync(0);
    await ending;

    expect(state.live).toEqual(second);
    expect(state.voiceErrorCode).toBe('');
  });
});
