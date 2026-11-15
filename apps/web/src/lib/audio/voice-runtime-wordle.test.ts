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
  voice: { id: 'voice-1', name: 'Voice', languages: [] },
  voice_language: 'en',
  voice_source: 'default',
};

afterEach(() => {
  vi.useRealTimers();
  rooms().length = 0;
});

describe('sarjy.wordle data messages', () => {
  async function connected() {
    vi.useFakeTimers();
    const runtime = new LiveKitVoiceRuntime();
    const events: unknown[] = [];
    runtime.subscribe((event) => events.push(event));
    const connecting = runtime.connect(config);
    await vi.advanceTimersByTimeAsync(1);
    const room = rooms().at(-1)!;
    room.remoteParticipants.set('agent', { isAgent: true, attributes: { 'lk.agent.state': 'listening' } });
    room.connectResolve!();
    await vi.advanceTimersByTimeAsync(100);
    await connecting;
    return { room, events };
  }
  const payload = (action: string) => new TextEncoder().encode(JSON.stringify({ action }));

  it('delivers an agent open signal to subscribers through the real DataReceived path', async () => {
    const { room, events } = await connected();
    room.emit('dataReceived', payload('open'), { isAgent: true }, undefined, 'sarjy.wordle');
    expect(events).toContainEqual(expect.objectContaining({ type: 'wordle', action: 'open' }));
  });

  it('ignores the signal from a non-agent participant or an unknown action', async () => {
    const { room, events } = await connected();
    room.emit('dataReceived', payload('open'), { isAgent: false }, undefined, 'sarjy.wordle');
    room.emit('dataReceived', payload('reveal'), { isAgent: true }, undefined, 'sarjy.wordle');
    expect(events.filter((event) => (event as { type: string }).type === 'wordle')).toEqual([]);
  });
});
