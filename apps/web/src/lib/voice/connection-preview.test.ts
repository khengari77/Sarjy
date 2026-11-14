import { afterEach, describe, expect, it, vi } from 'vitest';
import type { SessionConfig } from '../api';
import { initialVoiceState } from './connection';

const mocks = vi.hoisted(() => ({
  start: vi.fn(),
  end: vi.fn(async () => {}),
  openPreview: vi.fn(),
}));

vi.mock('../chat/session', () => ({
  startVoiceSession: mocks.start,
  endVoiceSession: mocks.end,
}));
vi.mock('./microphonePreview', () => ({
  openConnectingPreview: mocks.openPreview,
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
    registerTextStreamHandler() {}
  }
  (globalThis as unknown as { __previewRooms: FakeRoom[] }).__previewRooms = rooms;
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
const rooms = () => (globalThis as unknown as { __previewRooms: FakeRoom[] }).__previewRooms;

const config: SessionConfig = {
  id: 'sess-1',
  url: 'ws://fake.invalid',
  token: 'token',
  expires_at: new Date(Date.now() + 60000).toISOString(),
  voice: { id: 'voice-1', name: 'Voice', languages: ['en'] },
  voice_language: 'en',
  voice_source: 'default',
};

function preview(level = 0.42) {
  let stopped = false;
  return {
    level: vi.fn(() => (stopped ? 0 : level)),
    stop: vi.fn(() => {
      stopped = true;
    }),
  };
}

function harness() {
  const state = initialVoiceState();
  const runtime = new LiveKitVoiceRuntime();
  const meter = {
    startMicMeter: vi.fn(),
    stopMicMeter: vi.fn(),
    startOutputMeter: vi.fn(),
    stopOutputMeter: vi.fn(),
    setupMeters: vi.fn(),
    teardownMeters: vi.fn(),
  };
  const connection = createVoiceConnection(state, {
    ensureConversation: async () => 'c1',
    getConversationId: () => 'c1',
    refresh: async () => {},
    getRuntime: () => runtime,
    getLanguage: () => 'en',
    getVoice: () => ({ id: 'v1', name: 'Voice' }),
    applyCommand: () => {},
    resyncSettings: () => {},
    meters: meter,
  });
  return { state, connection, meter };
}

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
  rooms().length = 0;
});

describe('connecting microphone preview consent boundary', () => {
  it('never asks for the microphone before an explicit connect call', () => {
    harness();
    expect(mocks.openPreview).not.toHaveBeenCalled();
  });

  it('feeds a real level while connecting and stops when the session takes over', async () => {
    vi.useFakeTimers();
    mocks.start.mockResolvedValue(config);
    const fake = preview(0.42);
    mocks.openPreview.mockResolvedValue(fake);
    const { state, connection } = harness();

    const attempt = connection.connect(true);
    await vi.advanceTimersByTimeAsync(1);
    expect(mocks.openPreview).toHaveBeenCalledTimes(1);
    expect(state.connecting).toBe(true);
    expect(state.micLevel).toBe(0);

    await vi.advanceTimersByTimeAsync(90);
    expect(state.micLevel).toBeCloseTo(0.42);
    expect(fake.stop).not.toHaveBeenCalled();

    rooms().at(-1)!.remoteParticipants.set('agent', {
      isAgent: true,
      attributes: { 'lk.agent.state': 'listening' },
    });
    rooms().at(-1)!.connectResolve!();
    await vi.advanceTimersByTimeAsync(150);
    await attempt;

    expect(state.live).toEqual(config);
    expect(fake.stop).toHaveBeenCalledTimes(1);
    expect(state.micLevel).toBe(0);
  });

  it('stops the preview when the user cancels the connection', async () => {
    vi.useFakeTimers();
    let resolveStart!: (value: SessionConfig) => void;
    mocks.start.mockReturnValue(new Promise<SessionConfig>((resolve) => (resolveStart = resolve)));
    const fake = preview();
    mocks.openPreview.mockResolvedValue(fake);
    const { state, connection } = harness();

    const attempt = connection.connect(true);
    await vi.advanceTimersByTimeAsync(1);
    expect(state.connecting).toBe(true);

    await connection.cancelConnect();
    expect(fake.stop).toHaveBeenCalledTimes(1);
    expect(state.micLevel).toBe(0);

    resolveStart(config);
    await vi.advanceTimersByTimeAsync(1);
    await attempt;
    expect(state.live).toBeNull();
  });

  it('stops the preview when startup fails', async () => {
    vi.useFakeTimers();
    mocks.start.mockRejectedValue(new Error('PIPELINE_NOT_CONFIGURED'));
    const fake = preview();
    mocks.openPreview.mockResolvedValue(fake);
    const { state, connection } = harness();

    const attempt = connection.connect(true);
    await vi.advanceTimersByTimeAsync(1);
    await attempt;

    expect(fake.stop).toHaveBeenCalledTimes(1);
    expect(state.voiceErrorCode).toBe('PIPELINE_NOT_CONFIGURED');
    expect(state.micLevel).toBe(0);
    expect(state.connecting).toBe(false);
  });

  it('stops the preview on unmount', async () => {
    vi.useFakeTimers();
    mocks.start.mockReturnValue(new Promise<SessionConfig>(() => {}));
    const fake = preview();
    mocks.openPreview.mockResolvedValue(fake);
    const { connection } = harness();

    void connection.connect(true);
    await vi.advanceTimersByTimeAsync(1);
    connection.disposePreview();
    expect(fake.stop).toHaveBeenCalledTimes(1);
  });

  it('releases a capture that resolves after unmount', async () => {
    vi.useFakeTimers();
    mocks.start.mockReturnValue(new Promise<SessionConfig>(() => {}));
    let resolveOpen!: (value: ReturnType<typeof preview>) => void;
    mocks.openPreview.mockReturnValue(new Promise((resolve) => (resolveOpen = resolve)));
    const { state, connection } = harness();

    void connection.connect(true);
    await vi.advanceTimersByTimeAsync(1);
    connection.disposePreview();
    const late = preview(0.42);
    resolveOpen(late);
    await vi.advanceTimersByTimeAsync(200);

    expect(mocks.openPreview).toHaveBeenCalledTimes(1);
    expect(late.stop).toHaveBeenCalledTimes(1);
    expect(state.micLevel).toBe(0);
  });
});
