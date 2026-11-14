import { afterEach, describe, expect, it, vi } from 'vitest';
import type { SessionConfig } from '../api';

interface FakeRoom {
  connectResolve: (() => void) | null;
  connectReject: ((error: Error) => void) | null;
  remoteParticipants: Map<string, { isAgent: boolean; attributes: Record<string, string> }>;
  localParticipant: { getTrackPublication: () => { track: { stop(): void } } | undefined };
  emit(event: string, ...args: unknown[]): void;
  disconnect: () => Promise<void>;
}

vi.mock('livekit-client', () => {
  const rooms: FakeRoom[] = [];
  class Room {
    listeners = new Map<string, Set<(...args: unknown[]) => void>>();
    remoteParticipants = new Map<string, { isAgent: boolean; attributes: Record<string, string> }>();
    localParticipant = { getTrackPublication: () => undefined };
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
    off(event: string, handler: (...args: unknown[]) => void) {
      this.listeners.get(event)?.delete(handler);
    }
    unregisterTextStreamHandler() {}
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
    Track: { Kind: { Audio: 'audio' }, Source: { Microphone: 'microphone' } },
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
  vi.unstubAllGlobals();
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

it('releases audio even when room disconnect rejects', async () => {
  vi.useFakeTimers();
  vi.stubGlobal('window', {});
  vi.stubGlobal('document', { body: { appendChild: vi.fn() } });
  const runtime = new LiveKitVoiceRuntime();
  const connecting = runtime.connect(config);
  await vi.advanceTimersByTimeAsync(1);
  const room = rooms().at(-1)!;
  room.remoteParticipants.set('agent', { isAgent: true, attributes: { 'lk.agent.state': 'listening' } });
  room.connectResolve!();
  await vi.advanceTimersByTimeAsync(100);
  await connecting;
  const audio = { pause: vi.fn(), remove: vi.fn(), srcObject: {} };
  room.emit('trackSubscribed', { kind: 'audio', attach: () => audio });
  room.disconnect = async () => {
    throw new Error('disconnect failed');
  };
  await expect(runtime.disconnect()).resolves.toBeUndefined();
  expect(audio.pause).toHaveBeenCalledOnce();
  expect(audio.remove).toHaveBeenCalledOnce();
  expect(audio.srcObject).toBeNull();
  await runtime.disconnect();
  expect(audio.pause).toHaveBeenCalledOnce();
});

it('stops outgoing capture and playback before awaiting the previous room teardown', async () => {
  vi.useFakeTimers();
  vi.stubGlobal('window', {});
  vi.stubGlobal('document', { body: { appendChild: vi.fn() } });
  const runtime = new LiveKitVoiceRuntime();
  const connecting = runtime.connect(config);
  await vi.advanceTimersByTimeAsync(1);
  const outgoing = rooms().at(-1)!;
  outgoing.remoteParticipants.set('agent', { isAgent: true, attributes: { 'lk.agent.state': 'listening' } });
  outgoing.connectResolve!();
  await vi.advanceTimersByTimeAsync(100);
  await connecting;

  const sequence: string[] = [];
  const audio = {
    pause: vi.fn(() => sequence.push('detach-playback')),
    remove: vi.fn(),
    srcObject: {},
  };
  outgoing.emit('trackSubscribed', { kind: 'audio', attach: () => audio });
  const stop = vi.fn(() => sequence.push('stop-capture'));
  outgoing.localParticipant = { getTrackPublication: () => ({ track: { stop } }) };
  outgoing.disconnect = () => {
    sequence.push('await-teardown');
    // Never settles: the replacement must not wait for it.
    return new Promise<void>(() => {});
  };

  const replacement = runtime.connect({ ...config, id: 'sess-2' });
  await vi.advanceTimersByTimeAsync(1);

  // Ordering is the contract: outgoing media is released before the old
  // room teardown is awaited, not merely before the replacement finishes.
  expect(sequence).toEqual(['detach-playback', 'stop-capture', 'await-teardown']);
  expect(audio.srcObject).toBeNull();
  const next = rooms().at(-1)!;
  expect(next).not.toBe(outgoing);
  next.remoteParticipants.set('agent', { isAgent: true, attributes: { 'lk.agent.state': 'listening' } });
  next.connectResolve!();
  await vi.advanceTimersByTimeAsync(100);
  await expect(replacement).resolves.toBeUndefined();
});

it('resolves the replacement call when the previous teardown rejects', async () => {
  vi.useFakeTimers();
  vi.stubGlobal('window', {});
  vi.stubGlobal('document', { body: { appendChild: vi.fn() } });
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  class RejectingTeardownRuntime extends LiveKitVoiceRuntime {
    override async disconnect(): Promise<void> {
      throw new Error('sensitive provider detail');
    }
  }
  try {
    const runtime = new RejectingTeardownRuntime();
    const replacement = runtime.connect(config);
    await vi.advanceTimersByTimeAsync(1);
    const room = rooms().at(-1)!;
    room.remoteParticipants.set('agent', { isAgent: true, attributes: { 'lk.agent.state': 'listening' } });
    room.connectResolve!();
    await vi.advanceTimersByTimeAsync(100);

    await expect(replacement).resolves.toBeUndefined();
    expect(warn).toHaveBeenCalledWith('[Sarjy] Previous voice call teardown failed', 'Error');
    expect(JSON.stringify(warn.mock.calls)).not.toContain('sensitive');
    // The new call is usable, not just non-throwing.
    expect(rooms().at(-1)).toBe(room);
    expect(runtime.epoch).toBeGreaterThan(0);
  } finally {
    warn.mockRestore();
  }
});
