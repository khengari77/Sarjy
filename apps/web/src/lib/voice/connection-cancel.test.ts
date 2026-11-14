import { afterEach, expect, it, vi } from 'vitest';
import type { SessionConfig } from '../api';
import { initialVoiceState } from './connection';
import { createSelectionController } from './selection';
import type { ConversationStore } from './conversations';

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
  disconnectWait?: Promise<void>;
  failMicrophone?: boolean;
  remoteParticipants: Map<string, { isAgent: boolean; attributes: Record<string, string> }>;
  emit(event: string, ...args: unknown[]): void;
}

vi.mock('livekit-client', () => {
  const rooms: FakeRoom[] = [];
  class Room {
    listeners = new Map<string, Set<(...args: unknown[]) => void>>();
    remoteParticipants = new Map<string, { isAgent: boolean; attributes: Record<string, string> }>();
    failMicrophone = false;
    disconnectWait?: Promise<void>;
    localParticipant = {
      getTrackPublication: () => undefined,
      setMicrophoneEnabled: async () => {
        if (this.failMicrophone) throw Object.assign(new Error('denied'), { name: 'NotAllowedError' });
      },
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
    async disconnect() {
      await this.disconnectWait;
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
  vi.unstubAllGlobals();
  rooms().length = 0;
});

it.each(['cancel', 'end', 'switch'])('%s completes local teardown without waiting for the room', async (action) => {
  vi.useFakeTimers();
  vi.stubGlobal('window', {});
  vi.stubGlobal('document', { body: { appendChild: vi.fn() } });
  mocks.start.mockResolvedValueOnce(config).mockResolvedValueOnce({ ...config, id: 'replacement' });
  const { connection, state } = harness();
  const starting = connection.connect(false);
  await vi.advanceTimersByTimeAsync(1);
  const old = rooms().at(-1)!;
  let finish!: () => void;
  old.disconnectWait = new Promise<void>((resolve) => {
    finish = resolve;
  });
  const audio = { pause: vi.fn(), remove: vi.fn(), srcObject: {} };
  old.emit('trackSubscribed', { kind: 'audio', attach: () => audio });
  if (action === 'end') {
    old.remoteParticipants.set('agent', { isAgent: true, attributes: { 'lk.agent.state': 'listening' } });
    old.connectResolve!();
    await vi.advanceTimersByTimeAsync(100);
    await starting;
  }
  let finished = false;
  const select = vi.fn(async () => {});
  const selection = createSelectionController({
    voice: state,
    getPending: () => null,
    setPending: () => {},
    run: async (work) => {
      await work();
      finished = true;
    },
    cancelConnect: () => connection.cancelConnect(),
    endVoice: () => connection.end(),
    getConversations: () => ({ state: { current: 'c1' }, select }) as unknown as ConversationStore,
  });
  const ending =
    action === 'switch'
      ? Promise.resolve(selection.requestSelect('c2'))
      : (action === 'cancel' ? connection.cancelConnect() : connection.end()).then(() => {
          finished = true;
        });
  try {
    await vi.advanceTimersByTimeAsync(1);
    expect(finished).toBe(true);
    expect(audio.pause).toHaveBeenCalledOnce();
    expect(audio.srcObject).toBeNull();
    if (action === 'end') expect(mocks.end).toHaveBeenCalledWith(config);
    if (action === 'switch') expect(select).toHaveBeenCalledWith('c2');
    // This is the continuation awaited by conversation selection.
    await ending;
    const replacement = connection.connect(false);
    await vi.advanceTimersByTimeAsync(1);
    const next = rooms().at(-1)!;
    next.remoteParticipants.set('agent', { isAgent: true, attributes: { 'lk.agent.state': 'listening' } });
    next.connectResolve!();
    await vi.advanceTimersByTimeAsync(100);
    await replacement;
    expect(state.live?.id).toBe('replacement');
    old.emit('disconnected');
    expect(state.live?.id).toBe('replacement');
    await connection.end();
  } finally {
    finish();
    old.connectResolve!();
    await vi.advanceTimersByTimeAsync(100);
    await starting;
    await ending;
  }
});
