import { afterEach, describe, expect, it, vi } from 'vitest';
import type { SessionConfig } from '../api';

interface FakeElement {
  pause: ReturnType<typeof vi.fn>;
  remove: ReturnType<typeof vi.fn>;
  srcObject: unknown;
}

interface FakeRoom {
  connectResolve: (() => void) | null;
  connectReject: ((error: Error) => void) | null;
  remoteParticipants: Map<string, { isAgent: boolean; attributes: Record<string, string> }>;
  localParticipant: { identity: string; setMicrophoneEnabled: ReturnType<typeof vi.fn> };
  disconnectGate: Promise<void> | null;
  emit(event: string, ...args: unknown[]): void;
  emitTextStream(topic: string, reader: unknown, participantInfo: { identity: string }): void;
  disconnect: () => Promise<void>;
}

const appended: FakeElement[] = [];
(globalThis as unknown as { document: { body: { appendChild(element: FakeElement): void } } }).document = {
  body: { appendChild: (element) => appended.push(element) },
};
(globalThis as unknown as { window: Record<string, unknown> }).window = {};

vi.mock('livekit-client', () => {
  const rooms: FakeRoom[] = [];
  class Room {
    listeners = new Map<string, Set<(...args: unknown[]) => void>>();
    remoteParticipants = new Map<string, { isAgent: boolean; attributes: Record<string, string> }>();
    localParticipant = { identity: 'user-1', setMicrophoneEnabled: vi.fn(async () => {}) };
    textHandlers = new Map<string, (reader: unknown, participantInfo: { identity: string }) => void>();
    canPlaybackAudio = true;
    connectResolve: (() => void) | null = null;
    connectReject: ((error: Error) => void) | null = null;
    disconnectGate: Promise<void> | null = null;
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
      return new Promise<void>((resolve, reject) => {
        this.connectResolve = resolve;
        this.connectReject = reject;
      });
    }
    async startAudio() {}
    async disconnect() {
      if (this.disconnectGate) await this.disconnectGate;
    }
    removeAllListeners() {
      this.listeners.clear();
    }
    registerTextStreamHandler(topic: string, handler: (reader: unknown, participantInfo: { identity: string }) => void) {
      this.textHandlers.set(topic, handler);
    }
    emitTextStream(topic: string, reader: unknown, participantInfo: { identity: string }) {
      this.textHandlers.get(topic)?.(reader, participantInfo);
    }
  }
  (globalThis as unknown as { __fakeRooms: FakeRoom[] }).__fakeRooms = rooms;
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

const { LiveKitVoiceRuntime } = await import('./voice-runtime');
const rooms = () => (globalThis as unknown as { __fakeRooms: FakeRoom[] }).__fakeRooms;
const config: SessionConfig = {
  id: 'sess-1',
  url: 'ws://fake.invalid',
  token: 'token',
  expires_at: new Date(Date.now() + 60000).toISOString(),
};

afterEach(() => {
  vi.useRealTimers();
  rooms().length = 0;
});

describe('startup terminal disconnection', () => {
  it('rejects promptly after a disconnect instead of waiting for the 45-second timeout', async () => {
    vi.useFakeTimers();
    const runtime = new LiveKitVoiceRuntime();
    const connecting = runtime.connect(config);
    await vi.advanceTimersByTimeAsync(1);
    const room = rooms().at(-1)!;
    room.connectResolve!();
    await vi.advanceTimersByTimeAsync(0);
    room.emit('disconnected');
    await expect(connecting).rejects.toThrow('CONNECTION_LOST');
  });

  it('does not abort a viable session for transient reconnecting/reconnected events', async () => {
    vi.useFakeTimers();
    const runtime = new LiveKitVoiceRuntime();
    const connecting = runtime.connect(config);
    await vi.advanceTimersByTimeAsync(1);
    const room = rooms().at(-1)!;
    room.connectResolve!();
    await vi.advanceTimersByTimeAsync(0);
    room.emit('reconnecting');
    room.emit('reconnected');
    room.remoteParticipants.set('agent', { isAgent: true, attributes: { 'lk.agent.state': 'listening' } });
    await vi.advanceTimersByTimeAsync(100);
    await expect(connecting).resolves.toBeUndefined();
  });

  it('keeps explicit cancellation distinct from a terminal failure', async () => {
    vi.useFakeTimers();
    const runtime = new LiveKitVoiceRuntime();
    const connecting = runtime.connect(config);
    await vi.advanceTimersByTimeAsync(1);
    const room = rooms().at(-1)!;
    room.connectResolve!();
    await vi.advanceTimersByTimeAsync(0);
    await runtime.cancelConnect();
    await vi.advanceTimersByTimeAsync(0);
    await expect(connecting).resolves.toBeUndefined();
    expect(runtime.epoch).toBeGreaterThan(1);
  });
});

function fakeElement(): FakeElement {
  return { pause: vi.fn(), remove: vi.fn(), srcObject: null };
}

async function connectedRuntime(start: SessionConfig) {
  const runtime = new LiveKitVoiceRuntime();
  const connecting = runtime.connect(start);
  await vi.advanceTimersByTimeAsync(1);
  const room = rooms().at(-1)!;
  room.remoteParticipants.set('agent', { isAgent: true, attributes: { 'lk.agent.state': 'listening' } });
  room.connectResolve!();
  await vi.advanceTimersByTimeAsync(100);
  await connecting;
  return { runtime, room };
}

describe('teardown generation', () => {
  it('does not remove a replacement room attached during an in-flight disconnect', async () => {
    vi.useFakeTimers();
    const { runtime, room: roomA } = await connectedRuntime(config);
    const elementA = fakeElement();
    roomA.emit('trackSubscribed', { kind: 'audio', attach: () => elementA, detach: () => [elementA] });

    let releaseA!: () => void;
    roomA.disconnectGate = new Promise<void>((resolve) => {
      releaseA = resolve;
    });
    const teardown = runtime.disconnect();

    const second = runtime.connect({ ...config, id: 'sess-2' });
    await vi.advanceTimersByTimeAsync(1);
    const roomB = rooms().at(-1)!;
    roomB.remoteParticipants.set('agent', { isAgent: true, attributes: { 'lk.agent.state': 'listening' } });
    roomB.connectResolve!();
    await vi.advanceTimersByTimeAsync(100);
    await second;

    const elementB = fakeElement();
    roomB.emit('trackSubscribed', { kind: 'audio', attach: () => elementB, detach: () => [elementB] });

    releaseA();
    await teardown;

    expect(elementA.remove).toHaveBeenCalledTimes(1);
    expect(elementB.remove).not.toHaveBeenCalled();
    expect(roomA.localParticipant.setMicrophoneEnabled).toHaveBeenCalledWith(false);
    expect(roomB.localParticipant.setMicrophoneEnabled).not.toHaveBeenCalledWith(false);
  });
});

describe('text-stream transcription metadata', () => {
  it('keeps a user provisional segment as user/false with its segment id', async () => {
    vi.useFakeTimers();
    const { runtime, room } = await connectedRuntime(config);
    const events: Array<{ type: string; text?: string; final?: boolean; segmentId?: string; speaker?: string }> = [];
    runtime.subscribe((event) => events.push(event));

    room.emitTextStream(
      'lk.transcription',
      {
        info: { id: 'stream-1', attributes: { 'lk.transcription_final': 'false', 'lk.segment_id': 'user-partial' } },
        readAll: async () => 'hel',
      },
      { identity: 'user-1' },
    );
    await vi.advanceTimersByTimeAsync(0);

    expect(events.at(-1)).toMatchObject({
      type: 'transcript',
      text: 'hel',
      final: false,
      segmentId: 'user-partial',
      speaker: 'user',
    });
  });
});
