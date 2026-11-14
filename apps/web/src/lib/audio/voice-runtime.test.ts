import { afterEach, describe, expect, it, vi } from 'vitest';
import type { SessionConfig } from '../api';

interface FakeRoom {
  connectResolve: (() => void) | null;
  connectReject: ((error: Error) => void) | null;
  remoteParticipants: Map<string, { isAgent: boolean; attributes: Record<string, string> }>;
  emit(event: string, ...args: unknown[]): void;
  disconnect: () => Promise<void>;
}

vi.mock('livekit-client', () => {
  const rooms: FakeRoom[] = [];
  class Room {
    listeners = new Map<string, Set<(...args: unknown[]) => void>>();
    remoteParticipants = new Map<string, { isAgent: boolean; attributes: Record<string, string> }>();
    localParticipant = {};
    canPlaybackAudio = true;
    connectResolve: (() => void) | null = null;
    connectReject: ((error: Error) => void) | null = null;
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
    async disconnect() {}
    removeAllListeners() {
      this.listeners.clear();
    }
    registerTextStreamHandler() {}
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
