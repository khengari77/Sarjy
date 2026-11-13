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

vi.mock('../audio/microphone', () => ({
  ensureMicrophoneAccess: vi.fn(async () => {}),
}));

interface FakeElement {
  pause: ReturnType<typeof vi.fn>;
  remove: ReturnType<typeof vi.fn>;
  srcObject: unknown;
}

interface FakeRoom {
  connectResolve: (() => void) | null;
  remoteParticipants: Map<string, { isAgent: boolean; attributes: Record<string, string> }>;
  localParticipant: { identity: string; setMicrophoneEnabled: ReturnType<typeof vi.fn> };
  micStartRejects: boolean;
  disconnectGate: Promise<void> | null;
  emit(event: string, ...args: unknown[]): void;
}

(globalThis as unknown as { document: { body: { appendChild(element: FakeElement): void } } }).document = {
  body: { appendChild: () => {} },
};
(globalThis as unknown as { window: Record<string, unknown> }).window = {};

vi.mock('livekit-client', () => {
  const rooms: FakeRoom[] = [];
  class Room {
    listeners = new Map<string, Set<(...args: unknown[]) => void>>();
    remoteParticipants = new Map<string, { isAgent: boolean; attributes: Record<string, string> }>();
    localParticipant = {
      identity: 'user-1',
      setMicrophoneEnabled: vi.fn(async (enabled: boolean) => {
        if (enabled && this.micStartRejects) throw new Error('MIC_START_FAILED');
      }),
    };
    micStartRejects = false;
    disconnectGate: Promise<void> | null = null;
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
    async disconnect() {
      if (this.disconnectGate) await this.disconnectGate;
    }
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
  const meter = meters();
  const connection = createVoiceConnection(state, {
    ensureConversation: async () => 'c1',
    getConversationId: () => 'c1',
    refresh: async () => {},
    getRuntime: () => runtime,
    getLanguage: () => 'en',
    getVoice: () => ({ id: 'v1', name: 'Voice' }),
    meters: meter,
  });
  return { state, runtime, meter, connection };
}

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
  rooms().length = 0;
});

describe('terminal startup failure cleanup', () => {
  it('releases the exact failed session once and clears the attempt state', async () => {
    vi.useFakeTimers();
    mocks.start.mockResolvedValue(config);
    const { state, connection, meter } = harness();
    const connecting = connection.connect(false);
    await vi.advanceTimersByTimeAsync(1);
    rooms().at(-1)!.connectResolve!();
    await vi.advanceTimersByTimeAsync(0);
    rooms().at(-1)!.emit('disconnected');
    await connecting;

    expect(state.voiceErrorCode).toBe('CONNECTION_LOST');
    expect(state.live).toBeNull();
    expect(state.connecting).toBe(false);
    expect(mocks.end).toHaveBeenCalledTimes(1);
    expect(mocks.end).toHaveBeenCalledWith(config);
    expect(meter.setupMeters).not.toHaveBeenCalled();
  });

  it('allows a replacement attempt to become live after the failed cleanup', async () => {
    vi.useFakeTimers();
    const second: SessionConfig = { ...config, id: 'sess-2' };
    mocks.start.mockResolvedValueOnce(config).mockResolvedValueOnce(second);
    const { state, connection } = harness();
    const first = connection.connect(false);
    await vi.advanceTimersByTimeAsync(1);
    rooms().at(-1)!.connectResolve!();
    await vi.advanceTimersByTimeAsync(0);
    rooms().at(-1)!.emit('disconnected');
    await first;

    const secondAttempt = connection.connect(false);
    await vi.advanceTimersByTimeAsync(1);
    const replacement = rooms().at(-1)!;
    replacement.connectResolve!();
    replacement.remoteParticipants.set('agent', { isAgent: true, attributes: { 'lk.agent.state': 'listening' } });
    await vi.advanceTimersByTimeAsync(100);
    await secondAttempt;

    expect(state.live).toEqual(second);
    expect(state.voiceErrorCode).toBe('');
    expect(mocks.end).toHaveBeenCalledTimes(1);
    expect(mocks.end).toHaveBeenCalledWith(config);
  });
});

function fakeElement(): FakeElement {
  return { pause: vi.fn(), remove: vi.fn(), srcObject: null };
}

describe('stalled cleanup isolation', () => {
  it('keeps a replacement call live and unmuted when the failed attempt cleanup stalls', async () => {
    vi.useFakeTimers();
    const second: SessionConfig = { ...config, id: 'sess-2' };
    mocks.start.mockResolvedValueOnce(config).mockResolvedValueOnce(second);
    const { state, connection } = harness();

    const first = connection.connect(true);
    await vi.advanceTimersByTimeAsync(1);
    const roomA = rooms().at(-1)!;
    roomA.remoteParticipants.set('agent', { isAgent: true, attributes: { 'lk.agent.state': 'listening' } });
    roomA.micStartRejects = true;
    let releaseA!: () => void;
    roomA.disconnectGate = new Promise<void>((resolve) => {
      releaseA = resolve;
    });
    roomA.connectResolve!();

    const elementA = fakeElement();
    roomA.emit('trackSubscribed', { kind: 'audio', attach: () => elementA, detach: () => [elementA] });
    await vi.advanceTimersByTimeAsync(0);
    expect(roomA.localParticipant.setMicrophoneEnabled).toHaveBeenCalledWith(false);

    await connection.cancelConnect();
    const replacement = connection.connect(true);
    await vi.advanceTimersByTimeAsync(1);
    const roomB = rooms().at(-1)!;
    roomB.remoteParticipants.set('agent', { isAgent: true, attributes: { 'lk.agent.state': 'listening' } });
    roomB.connectResolve!();
    await vi.advanceTimersByTimeAsync(100);
    await replacement;

    const elementB = fakeElement();
    roomB.emit('trackSubscribed', { kind: 'audio', attach: () => elementB, detach: () => [elementB] });

    releaseA();
    await first;
    await vi.advanceTimersByTimeAsync(0);

    expect(elementA.remove).toHaveBeenCalledTimes(1);
    expect(elementB.remove).not.toHaveBeenCalled();
    expect(roomB.localParticipant.setMicrophoneEnabled).not.toHaveBeenCalledWith(false);
    expect(state.live).toEqual(second);
    expect(state.muted).toBe(false);
    expect(state.voiceErrorCode).toBe('');
  });
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
