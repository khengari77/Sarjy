import { vi } from 'vitest';
import type { SessionConfig } from '../api';
import type { VoiceRuntime } from '../audio/voice-runtime';
import { createVoiceConnection, initialVoiceState } from './connection';
import type { GreetingPlayer } from './greeting';

export const config: SessionConfig = {
  id: 'sess-1',
  url: 'ws://fake.invalid',
  token: 'token',
  expires_at: new Date(Date.now() + 60000).toISOString(),
  voice: { id: 'voice-1', name: 'Voice', languages: [] },
  voice_language: 'en',
  voice_source: 'default',
};

export const full: SessionConfig = {
  ...config,
  intro: { kind: 'full', text: 'The full introduction.', asset: 'voice-1/full.mp3' },
};
export const short: SessionConfig = {
  ...config,
  intro: { kind: 'short', text: 'Hi again.', asset: 'voice-1/short.mp3' },
};

export function deferred<T = void>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

export function fakePlayer() {
  let resolvePlay: ((outcome: 'ended' | 'failed') => void) | null = null;
  return {
    prepare: vi.fn(),
    play: vi.fn(() => {
      return new Promise<'ended' | 'failed'>((resolve) => {
        resolvePlay = resolve;
      });
    }),
    stop: vi.fn(() => {
      resolvePlay?.('failed');
      resolvePlay = null;
    }),
    get playing() {
      return resolvePlay !== null;
    },
    finish(outcome: 'ended' | 'failed') {
      resolvePlay?.(outcome);
      resolvePlay = null;
    },
  } satisfies GreetingPlayer & { finish(outcome: 'ended' | 'failed'): void };
}

export class FakeRuntime {
  epoch = 0;
  connects = 0;
  micStarts = 0;
  interrupts = 0;
  disconnects = 0;
  texts: string[] = [];
  connectGate: Promise<void> = Promise.resolve();
  micError: Error | null = null;

  async connect() {
    this.connects += 1;
    await this.connectGate;
  }
  async startMicrophone() {
    this.micStarts += 1;
    if (this.micError) throw this.micError;
  }
  async stopMicrophone() {}
  async interrupt() {
    this.interrupts += 1;
  }
  async sendText(text: string) {
    this.texts.push(text);
  }
  async disconnect() {
    this.disconnects += 1;
  }
  async cancelConnect() {}
  subscribe() {
    return () => {};
  }
}

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

export function harness<P extends GreetingPlayer = ReturnType<typeof fakePlayer>>(
  player: P = fakePlayer() as unknown as P,
  greetingMaxWaitMs?: number,
) {
  allowMicrophone();
  const state = initialVoiceState();
  const runtime = new FakeRuntime();
  const connection = createVoiceConnection(state, {
    ensureConversation: async () => 'c1',
    getConversationId: () => 'c1',
    refresh: async () => {},
    getRuntime: () => runtime as unknown as VoiceRuntime,
    getLanguage: () => 'en',
    getVoice: () => ({ id: 'v1', name: 'Voice' }),
    applyCommand: () => {},
    resyncSettings: () => {},
    meters: meters(),
    greetingPlayer: player,
    greetingMaxWaitMs,
  });
  return { state, runtime, player, connection };
}

export function allowMicrophone() {
  vi.stubGlobal('navigator', { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [] }) } });
}
