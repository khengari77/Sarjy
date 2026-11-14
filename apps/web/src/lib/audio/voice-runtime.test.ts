import { afterEach, describe, expect, it, vi } from 'vitest';
import type { SessionConfig } from '../api';

interface FakeRoom {
  connectResolve: (() => void) | null;
  connectReject: ((error: Error) => void) | null;
  remoteParticipants: Map<string, { isAgent: boolean; identity: string; attributes: Record<string, string> }>;
  localParticipant: {
    performRpc?: (options: Record<string, unknown>) => Promise<string>;
    publishData?: (data: Uint8Array, options: Record<string, unknown>) => Promise<void>;
  };
  emit(event: string, ...args: unknown[]): void;
  disconnect: () => Promise<void>;
}

vi.mock('livekit-client', () => {
  const rooms: FakeRoom[] = [];
  class Room {
    listeners = new Map<string, Set<(...args: unknown[]) => void>>();
    remoteParticipants = new Map<string, { isAgent: boolean; identity: string; attributes: Record<string, string> }>();
    localParticipant: FakeRoom['localParticipant'] = {};
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
    room.remoteParticipants.set('agent', {
      isAgent: true,
      identity: 'agent',
      attributes: { 'lk.agent.state': 'listening' },
    });
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

async function connected() {
  vi.useFakeTimers();
  const runtime = new LiveKitVoiceRuntime();
  const connecting = runtime.connect(config);
  await vi.advanceTimersByTimeAsync(1);
  const room = rooms().at(-1)!;
  room.connectResolve!();
  await vi.advanceTimersByTimeAsync(0);
  room.remoteParticipants.set('agent', {
    isAgent: true,
    identity: 'agent',
    attributes: { 'lk.agent.state': 'listening' },
  });
  await vi.advanceTimersByTimeAsync(100);
  await connecting;
  return { runtime, room };
}

describe('exact command speech', () => {
  it('sends the exact text to the agent sarjy.say RPC', async () => {
    const { runtime, room } = await connected();
    const performRpc = vi.fn().mockResolvedValue('{"ok":true}');
    room.localParticipant.performRpc = performRpc;
    await runtime.say('The answer was PLANT.');
    expect(performRpc).toHaveBeenCalledTimes(1);
    expect(performRpc).toHaveBeenCalledWith({
      destinationIdentity: 'agent',
      method: 'sarjy.say',
      payload: JSON.stringify({ text: 'The answer was PLANT.' }),
    });
  });

  it('refuses to speak without a live room', async () => {
    const runtime = new LiveKitVoiceRuntime();
    await expect(runtime.say('Hello')).rejects.toThrow('CALL_REQUIRED');
  });

  it('publishes control flags on the existing data channel and ignores no-room calls', async () => {
    const { runtime, room } = await connected();
    const publishData = vi.fn().mockResolvedValue(undefined);
    room.localParticipant.publishData = publishData;
    await runtime.sendControl({ type: 'wordle.reveal_pending', value: true });
    expect(publishData).toHaveBeenCalledTimes(1);
    const [data, options] = publishData.mock.calls[0];
    expect(JSON.parse(new TextDecoder().decode(data))).toEqual({ type: 'wordle.reveal_pending', value: true });
    expect(options).toEqual({ reliable: true, topic: 'sarjy.control' });

    const offline = new LiveKitVoiceRuntime();
    await expect(offline.sendControl({ type: 'wordle.reveal_pending', value: false })).resolves.toBeUndefined();
  });
});
