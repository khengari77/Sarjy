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
  localParticipant: { setMicrophoneEnabled: () => Promise<void> };
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

describe('startup release results are collected', () => {
  it('reports a failed release when an attempt is superseded before the transport connects', async () => {
    vi.useFakeTimers();
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    try {
      let resolveStart!: (session: SessionConfig) => void;
      mocks.start.mockImplementationOnce(
        () =>
          new Promise<SessionConfig>((resolve) => {
            resolveStart = resolve;
          }),
      );
      mocks.end.mockRejectedValueOnce(new Error('503'));
      const { connection } = harness();

      const attempt = connection.connect(false);
      await vi.advanceTimersByTimeAsync(0);
      const cancelling = connection.cancelConnect();
      resolveStart(config);
      await vi.advanceTimersByTimeAsync(0);
      await attempt;
      await cancelling;

      expect(mocks.end).toHaveBeenCalledWith(config);
      expect(warn).toHaveBeenCalledWith(
        '[Sarjy] Voice session release failed',
        expect.objectContaining({ sessionId: 'sess-1', path: 'superseded-before-connect' }),
      );
    } finally {
      warn.mockRestore();
    }
  });

  it('reports a failed release when an attempt is superseded while connecting', async () => {
    vi.useFakeTimers();
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    try {
      mocks.start.mockResolvedValueOnce(config);
      mocks.end.mockRejectedValueOnce(new Error('503')).mockRejectedValueOnce(new Error('503'));
      const { connection } = harness();

      const attempt = connection.connect(false);
      await vi.advanceTimersByTimeAsync(1);
      const room = rooms().at(-1)!;
      const cancelling = connection.cancelConnect();
      await vi.advanceTimersByTimeAsync(0);
      room.connectResolve!();
      await vi.advanceTimersByTimeAsync(0);
      await attempt;
      await cancelling;

      expect(warn).toHaveBeenCalledWith(
        '[Sarjy] Voice session release failed',
        expect.objectContaining({ sessionId: 'sess-1', path: 'superseded-during-connect' }),
      );
    } finally {
      warn.mockRestore();
    }
  });

  it('reports a failed release when an attempt is superseded during microphone start', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('navigator', { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [] }) } });
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    try {
      mocks.start.mockResolvedValueOnce(config);
      mocks.end.mockRejectedValueOnce(new Error('503')).mockRejectedValueOnce(new Error('503'));
      const { connection } = harness();

      const attempt = connection.connect(true);
      await vi.advanceTimersByTimeAsync(1);
      const room = rooms().at(-1)!;
      let finishMicrophone!: () => void;
      room.localParticipant.setMicrophoneEnabled = () =>
        new Promise<void>((resolve) => {
          finishMicrophone = resolve;
        });
      room.remoteParticipants.set('agent', { isAgent: true, attributes: { 'lk.agent.state': 'listening' } });
      room.connectResolve!();
      await vi.advanceTimersByTimeAsync(100);

      const cancelling = connection.cancelConnect();
      await vi.advanceTimersByTimeAsync(0);
      finishMicrophone();
      await vi.advanceTimersByTimeAsync(0);
      await attempt;
      await cancelling;

      expect(warn).toHaveBeenCalledWith(
        '[Sarjy] Voice session release failed',
        expect.objectContaining({ sessionId: 'sess-1', path: 'superseded-during-microphone' }),
      );
    } finally {
      warn.mockRestore();
    }
  });

  it('reports a failed release when startup fails terminally', async () => {
    vi.useFakeTimers();
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    try {
      mocks.start.mockResolvedValueOnce(config);
      mocks.end.mockRejectedValueOnce(new Error('503'));
      const { state, connection } = harness();

      const attempt = connection.connect(false);
      await vi.advanceTimersByTimeAsync(1);
      const room = rooms().at(-1)!;
      room.connectResolve!();
      await vi.advanceTimersByTimeAsync(0);
      room.emit('disconnected');
      await attempt;

      expect(state.voiceErrorCode).toBe('CONNECTION_LOST');
      expect(warn).toHaveBeenCalledWith(
        '[Sarjy] Voice session release failed',
        expect.objectContaining({ sessionId: 'sess-1', path: 'startup-failed' }),
      );
    } finally {
      warn.mockRestore();
    }
  });
});
