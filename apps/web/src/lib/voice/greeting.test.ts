import { afterEach, describe, expect, it, vi } from 'vitest';
import { createBrowserGreetingPlayer, GREETING_MAX_PLAY_MS } from './greeting';

const audios: FakeAudio[] = [];

class FakeAudio {
  muted = false;
  preload = '';
  src = '';
  private active = new Map<string, Set<() => void>>();
  private registered = new Map<string, Set<() => void>>();

  constructor() {
    audios.push(this);
  }

  private key(name: string, src = this.src) {
    return `${src}\u0000${name}`;
  }
  addEventListener(name: string, handler: () => void) {
    for (const map of [this.active, this.registered]) {
      const key = this.key(name);
      if (!map.has(key)) map.set(key, new Set());
      map.get(key)!.add(handler);
    }
  }
  removeEventListener(name: string, handler: () => void) {
    this.active.get(this.key(name))?.delete(handler);
  }
  emit(name: string) {
    this.emitFrom(this.src, name);
  }
  emitFrom(src: string, name: string) {
    for (const handler of this.registered.get(this.key(name, src)) ?? []) handler();
  }
  load() {}
  pause() {}
  removeAttribute() {}
  play() {
    return Promise.resolve();
  }
}

function installAudio() {
  audios.length = 0;
  vi.stubGlobal('Audio', FakeAudio);
  return () => audios[0];
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('browser greeting playback cap', () => {
  it('resolves failed when the clip never ends within the play cap', async () => {
    vi.useFakeTimers();
    installAudio();
    const player = createBrowserGreetingPlayer();
    let outcome: 'ended' | 'failed' | undefined;
    void player.play('/greetings/voice/full.mp3').then((value) => (outcome = value));

    await vi.advanceTimersByTimeAsync(GREETING_MAX_PLAY_MS - 1);
    expect(outcome).toBeUndefined();
    expect(player.playing).toBe(true);

    await vi.advanceTimersByTimeAsync(1);
    expect(outcome).toBe('failed');
    expect(player.playing).toBe(false);
  });

  it('a stale ended from the previous source cannot resolve the new playback', async () => {
    const audio = installAudio();
    const player = createBrowserGreetingPlayer();
    void player.play('/greetings/voice/old.mp3');
    const pause = vi.spyOn(audio(), 'pause');

    const second = player.play('/greetings/voice/new.mp3');
    expect(pause).toHaveBeenCalled();
    let outcome: 'ended' | 'failed' | undefined;
    void second.then((value) => (outcome = value));

    audio().emitFrom('/greetings/voice/old.mp3', 'ended');
    await Promise.resolve();
    expect(outcome).toBeUndefined();
    expect(player.playing).toBe(true);

    audio().emit('ended');
    await expect(second).resolves.toBe('ended');
  });

  it('honours an injected cap and clears it when the clip ends first', async () => {
    vi.useFakeTimers();
    const audio = installAudio();
    const player = createBrowserGreetingPlayer({ maxPlayMs: 1_000 });
    const first = player.play('/greetings/voice/short.mp3');
    audio().emit('ended');
    await expect(first).resolves.toBe('ended');

    const second = player.play('/greetings/voice/full.mp3');
    let outcome: 'ended' | 'failed' | undefined;
    void second.then((value) => (outcome = value));
    await vi.advanceTimersByTimeAsync(1_000);
    expect(outcome).toBe('failed');
  });
});
