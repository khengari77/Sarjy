import { afterEach, describe, expect, it, vi } from 'vitest';
import type { SessionConfig } from '../api';
import { initialVoiceState } from './connection';

const mocks = vi.hoisted(() => ({
  start: vi.fn(),
  end: vi.fn(async () => {}),
}));

vi.mock('../chat/session', () => ({
  startVoiceSession: mocks.start,
  markIntroComplete: vi.fn(),
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
    unregisterTextStreamHandler() {}
  }
  (globalThis as unknown as { __timingRooms: FakeRoom[] }).__timingRooms = rooms;
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
const rooms = () => (globalThis as unknown as { __timingRooms: FakeRoom[] }).__timingRooms;
const config: SessionConfig = {
  id: 'sess-1',
  url: 'ws://fake.invalid',
  token: 'token',
  expires_at: new Date(Date.now() + 60000).toISOString(),
  voice: { id: 'voice-1', name: 'Voice', languages: [] },
  voice_language: 'en',
  voice_source: 'default',
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
    applyCommand: () => {},
    resyncSettings: () => {},
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

function enableTiming() {
  vi.stubEnv('DEV', false);
  const windowStub = {
    localStorage: { getItem: (key: string) => (key === 'sarjy:call-timing' ? '1' : null) },
    __sarjyCallStart: undefined as unknown,
  };
  vi.stubGlobal('window', windowStub);
  vi.stubGlobal('navigator', { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [] }) } });
  return windowStub;
}

function reports(info: { mock: { calls: unknown[][] } }) {
  return info.mock.calls.filter((call: unknown[]) => call[0] === '[Sarjy call-start]');
}

function markNames(payload: unknown) {
  return Object.keys(payload as Record<string, unknown>).filter((key) => key !== 'steps');
}

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  rooms().length = 0;
});

describe('call-start mark wiring', () => {
  it('emits every mark in connect order', async () => {
    vi.useFakeTimers();
    const windowStub = enableTiming();
    const info = vi.spyOn(console, 'info').mockImplementation(() => {});
    mocks.start.mockResolvedValue(config);
    const { state, connection } = harness();

    const connecting = connection.connect(true);
    await vi.advanceTimersByTimeAsync(1);
    const room = rooms().at(-1)!;
    room.remoteParticipants.set('agent', { isAgent: true, attributes: { 'lk.agent.state': 'listening' } });
    room.connectResolve!();
    await vi.advanceTimersByTimeAsync(100);
    await connecting;

    expect(state.live).toEqual(config);
    const emitted = reports(info);
    expect(emitted).toHaveLength(1);
    expect(markNames(emitted[0][1])).toEqual([
      'press',
      'conversation_ready',
      'mic_ready',
      'session_request_start',
      'session_response',
      'room_joined',
      'agent_state_first',
      'room_connected',
      'mic_started',
    ]);
    expect(windowStub.__sarjyCallStart).toBe(emitted[0][1]);
  });

  it('reports one failure with only the marks reached so far', async () => {
    vi.useFakeTimers();
    const windowStub = enableTiming();
    vi.stubGlobal('navigator', {
      mediaDevices: {
        getUserMedia: async () => {
          throw Object.assign(new Error('denied'), { name: 'NotAllowedError' });
        },
      },
    });
    const info = vi.spyOn(console, 'info').mockImplementation(() => {});
    mocks.start.mockResolvedValue(config);
    const { state, connection } = harness();

    await connection.connect(true);

    expect(state.voiceErrorCode).toBe('MICROPHONE_PERMISSION_DENIED');
    expect(mocks.start).not.toHaveBeenCalled();
    const emitted = reports(info);
    expect(emitted).toHaveLength(1);
    expect(markNames(emitted[0][1])).toEqual(['press', 'conversation_ready']);
    expect(windowStub.__sarjyCallStart).toBe(emitted[0][1]);
  });

  it('drops a superseded attempt so only the replacement reaches the timer', async () => {
    vi.useFakeTimers();
    const windowStub = enableTiming();
    const info = vi.spyOn(console, 'info').mockImplementation(() => {});
    const second: SessionConfig = { ...config, id: 'sess-2' };
    let resolveFirst!: (session: SessionConfig) => void;
    mocks.start.mockImplementationOnce(
      () =>
        new Promise<SessionConfig>((resolve) => {
          resolveFirst = resolve;
        }),
    );
    const { state, connection } = harness();

    const first = connection.connect(true);
    await vi.advanceTimersByTimeAsync(1);
    await connection.cancelConnect();

    mocks.start.mockResolvedValueOnce(second);
    const replacement = connection.connect(true);
    await vi.advanceTimersByTimeAsync(1);
    expect(reports(info)).toHaveLength(0);

    const room = rooms().at(-1)!;
    room.remoteParticipants.set('agent', { isAgent: true, attributes: { 'lk.agent.state': 'listening' } });
    room.connectResolve!();
    await vi.advanceTimersByTimeAsync(100);
    await replacement;

    const emitted = reports(info);
    expect(emitted).toHaveLength(1);
    const replacementReport = emitted[0][1];

    // The cancelled attempt resolves its admission after the replacement reported: it must not
    // report or overwrite the newer call's window global.
    resolveFirst(config);
    await vi.advanceTimersByTimeAsync(1);
    await first;

    expect(reports(info)).toHaveLength(1);
    expect(state.live).toEqual(second);
    expect(windowStub.__sarjyCallStart).toBe(replacementReport);
  });
});
