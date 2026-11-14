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
  emit(event: string, ...args: unknown[]): void;
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

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  rooms().length = 0;
});

describe('failed startup cleanup', () => {
  it('keeps the admitted session retryable and re-releases it on demand', async () => {
    vi.useFakeTimers();
    mocks.start.mockResolvedValueOnce(config);
    mocks.end.mockRejectedValueOnce(new Error('503')).mockResolvedValueOnce(undefined);
    const { state, connection } = harness();

    const attempt = connection.connect(false);
    await vi.advanceTimersByTimeAsync(1);
    const room = rooms().at(-1)!;
    room.connectResolve!();
    await vi.advanceTimersByTimeAsync(0);
    room.emit('disconnected');
    await attempt;

    expect(state.voiceErrorCode).toBe('CONNECTION_LOST');
    expect(state.live).toBeNull();
    expect(state.pendingCleanup).toEqual(config);

    await connection.retryCleanup();

    expect(mocks.end).toHaveBeenLastCalledWith(config);
    expect(state.pendingCleanup).toBeNull();
  });

  it('keeps the outstanding cleanup when a new call starts', async () => {
    vi.useFakeTimers();
    const second: SessionConfig = { ...config, id: 'sess-2' };
    mocks.start.mockResolvedValueOnce(config).mockResolvedValueOnce(second);
    mocks.end.mockRejectedValueOnce(new Error('503')).mockResolvedValueOnce(undefined);
    const { state, connection } = harness();

    const failed = connection.connect(false);
    await vi.advanceTimersByTimeAsync(1);
    const firstRoom = rooms().at(-1)!;
    firstRoom.connectResolve!();
    await vi.advanceTimersByTimeAsync(0);
    firstRoom.emit('disconnected');
    await failed;
    expect(state.pendingCleanup).toEqual(config);

    const next = connection.connect(false);
    await vi.advanceTimersByTimeAsync(1);
    const secondRoom = rooms().at(-1)!;
    secondRoom.remoteParticipants.set('agent', { isAgent: true, attributes: { 'lk.agent.state': 'listening' } });
    secondRoom.connectResolve!();
    await vi.advanceTimersByTimeAsync(100);
    await next;

    // Starting a call must not silently drop the older admission.
    expect(state.live).toEqual(second);
    expect(state.pendingCleanup).toEqual(config);

    await connection.retryCleanup();
    expect(mocks.end).toHaveBeenLastCalledWith(config);
    expect(state.pendingCleanup).toBeNull();
    expect(state.live).toEqual(second);
  });
});
