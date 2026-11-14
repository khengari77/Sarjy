import { afterEach, describe, expect, it, vi } from 'vitest';
import type { SessionConfig } from '../api';

interface FakeRoom {
  connectResolve: (() => void) | null;
  connectReject: ((error: Error) => void) | null;
  remoteParticipants: Map<string, { isAgent: boolean; attributes: Record<string, string> }>;
  localParticipant: { identity: string; trackPublications: Map<string, { trackSid: string }> };
  emit(event: string, ...args: unknown[]): void;
  emitTextStream(topic: string, reader: unknown, participantInfo: { identity: string }): void;
  disconnect: () => Promise<void>;
}

vi.mock('livekit-client', () => {
  const rooms: FakeRoom[] = [];
  class Room {
    listeners = new Map<string, Set<(...args: unknown[]) => void>>();
    remoteParticipants = new Map<string, { isAgent: boolean; attributes: Record<string, string> }>();
    localParticipant = { identity: 'user-1', trackPublications: new Map<string, { trackSid: string }>() };
    textHandlers = new Map<string, (reader: unknown, participantInfo: { identity: string }) => void>();
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
    registerTextStreamHandler(
      topic: string,
      handler: (reader: unknown, participantInfo: { identity: string }) => void,
    ) {
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

describe('text-stream transcription metadata', () => {
  it('keeps a user provisional segment as user/false with its segment id', async () => {
    vi.useFakeTimers();
    const { runtime, room } = await connectedRuntime(config);
    room.localParticipant.trackPublications.set('TR_MIC', { trackSid: 'TR_MIC' });
    const events: Array<{ type: string; text?: string; final?: boolean; segmentId?: string; speaker?: string }> = [];
    runtime.subscribe((event) => events.push(event));

    room.emitTextStream(
      'lk.transcription',
      {
        info: {
          id: 'stream-1',
          attributes: {
            'lk.transcription_final': 'false',
            'lk.segment_id': 'user-partial',
            'lk.transcribed_track_id': 'TR_MIC',
          },
        },
        readAll: async () => 'hel',
      },
      // Publisher identity must not decide the speaker.
      { identity: 'agent-9' },
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

  it('labels a segment from a non-local transcribed track as assistant', async () => {
    vi.useFakeTimers();
    const { runtime, room } = await connectedRuntime(config);
    room.localParticipant.trackPublications.set('TR_MIC', { trackSid: 'TR_MIC' });
    const events: Array<{ type: string; speaker?: string }> = [];
    runtime.subscribe((event) => events.push(event));

    room.emitTextStream(
      'lk.transcription',
      {
        info: {
          id: 'stream-2',
          attributes: { 'lk.transcribed_track_id': 'TR_AGENT_OUTPUT', 'lk.transcription_final': 'true' },
        },
        readAll: async () => 'hello there',
      },
      // Even when the publisher is this participant, the track id decides.
      { identity: 'user-1' },
    );
    await vi.advanceTimersByTimeAsync(0);

    expect(events.at(-1)).toMatchObject({ type: 'transcript', text: 'hello there', speaker: 'assistant' });
  });

  it('treats a segment without a transcribed track id as assistant', async () => {
    vi.useFakeTimers();
    const { runtime, room } = await connectedRuntime(config);
    const events: Array<{ type: string; speaker?: string }> = [];
    runtime.subscribe((event) => events.push(event));

    room.emitTextStream(
      'lk.transcription',
      { info: { id: 'stream-3', attributes: {} }, readAll: async () => 'unknown source' },
      { identity: 'user-1' },
    );
    await vi.advanceTimersByTimeAsync(0);

    expect(events.at(-1)).toMatchObject({ type: 'transcript', speaker: 'assistant' });
  });
});
