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
      getTrackPublication: () => undefined,
      setMicrophoneEnabled: async () => {},
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
    off(event: string, handler: (...args: unknown[]) => void) {
      this.listeners.get(event)?.delete(handler);
    }
    unregisterTextStreamHandler() {}
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
    Track: { Kind: { Audio: 'audio' }, Source: { Microphone: 'microphone' } },
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
    meters: {
      startMicMeter: vi.fn(),
      stopMicMeter: vi.fn(),
      startOutputMeter: vi.fn(),
      stopOutputMeter: vi.fn(),
      setupMeters: vi.fn(),
      teardownMeters: vi.fn(),
    },
  });
  return { state, connection };
}

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  rooms().length = 0;
});

describe('session release failure', () => {
  it('keeps a failed release retryable instead of reporting idle', async () => {
    vi.useFakeTimers();
    mocks.start.mockResolvedValueOnce(config);
    mocks.end.mockRejectedValueOnce(new Error('503')).mockResolvedValueOnce(undefined);
    const { state, connection } = harness();

    const start = connection.connect(false);
    await vi.advanceTimersByTimeAsync(1);
    const room = rooms().at(-1)!;
    room.remoteParticipants.set('agent', { isAgent: true, attributes: { 'lk.agent.state': 'listening' } });
    room.connectResolve!();
    await vi.advanceTimersByTimeAsync(0);
    await start;
    expect(state.live).toEqual(config);

    await connection.end();
    expect(mocks.end).toHaveBeenCalledTimes(1);
    expect(state.voiceErrorCode).toBe('SESSION_RELEASE_FAILED');
    expect(state.live).toEqual(config);

    await connection.end();
    expect(mocks.end).toHaveBeenCalledTimes(2);
    expect(state.live).toBeNull();
    expect(state.voiceErrorCode).toBe('');
  });
});

describe('pending session release', () => {
  it('releases the server session even if the transport connect never resolves', async () => {
    vi.useFakeTimers();
    mocks.start.mockResolvedValueOnce(config);
    const { state, connection } = harness();

    const start = connection.connect(false);
    await vi.advanceTimersByTimeAsync(1);
    const room = rooms().at(-1)!;

    await connection.cancelConnect();
    room.connectResolve!();
    await vi.advanceTimersByTimeAsync(0);
    await start;

    expect(mocks.end).toHaveBeenCalledTimes(1);
    expect(mocks.end).toHaveBeenCalledWith(config);
    expect(state.live).toBeNull();
  });
});
