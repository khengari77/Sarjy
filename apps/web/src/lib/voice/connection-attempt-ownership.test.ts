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

describe('admission-window ownership', () => {
  it('does not restore a failed session while a replacement is still awaiting admission', async () => {
    vi.useFakeTimers();
    const second: SessionConfig = { ...config, id: 'sess-2' };
    const { state, connection } = await liveConnection();
    let resolveStart!: (session: SessionConfig) => void;
    mocks.start.mockImplementationOnce(
      () =>
        new Promise<SessionConfig>((resolve) => {
          resolveStart = resolve;
        }),
    );
    let failOld!: (error: Error) => void;
    mocks.end.mockImplementationOnce(
      () =>
        new Promise<void>((_resolve, reject) => {
          failOld = reject;
        }),
    );

    const ending = connection.end();
    await vi.advanceTimersByTimeAsync(0);
    expect(state.live).toBeNull();

    // B has started, but its admission response has not arrived: no id yet.
    const connecting = connection.connect(false);
    await vi.advanceTimersByTimeAsync(1);

    failOld(new Error('503'));
    await vi.advanceTimersByTimeAsync(0);

    expect(state.live).toBeNull();
    expect(state.pendingCleanup).toEqual(config);

    resolveStart(second);
    await vi.advanceTimersByTimeAsync(1);
    const room = rooms().at(-1)!;
    room.remoteParticipants.set('agent', { isAgent: true, attributes: { 'lk.agent.state': 'listening' } });
    room.connectResolve!();
    await vi.advanceTimersByTimeAsync(100);
    await connecting;
    await ending;

    expect(state.live).toEqual(second);
  });

  it('ignores a cancelled admission that arrives after a replacement took ownership', async () => {
    vi.useFakeTimers();
    const second: SessionConfig = { ...config, id: 'sess-2' };
    const { state, connection } = harness();
    let resolveFirst!: (session: SessionConfig) => void;
    mocks.start.mockImplementationOnce(
      () =>
        new Promise<SessionConfig>((resolve) => {
          resolveFirst = resolve;
        }),
    );

    const first = connection.connect(false);
    await vi.advanceTimersByTimeAsync(1);
    await connection.cancelConnect();

    mocks.start.mockResolvedValueOnce(second);
    const replacement = connection.connect(false);
    await vi.advanceTimersByTimeAsync(1);

    // The cancelled attempt's admission arrives late and must not claim
    // pending ownership; it releases its own session instead.
    resolveFirst(config);
    await vi.advanceTimersByTimeAsync(1);
    expect(mocks.end).toHaveBeenCalledWith(config);
    expect(mocks.end).not.toHaveBeenCalledWith(second);

    await connection.end();
    expect(mocks.end).toHaveBeenLastCalledWith(second);
    expect(state.live).toBeNull();

    rooms().at(-1)!.connectResolve?.();
    await vi.advanceTimersByTimeAsync(100);
    await replacement;
    await first;
  });
});
