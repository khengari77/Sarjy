import { expect, it, vi } from 'vitest';
import { disposeAudioResources } from './disposeAudio';

it.each(['source', 'analyser', 'pause', 'stream', 'remove', 'close-throw', 'close-reject'])(
  'continues disposal after %s fails',
  async (failure) => {
    const fail = () => {
      throw new Error('cleanup failed');
    };
    const source = { disconnect: vi.fn(failure === 'source' ? fail : () => {}) };
    const analyser = { disconnect: vi.fn(failure === 'analyser' ? fail : () => {}) };
    const pause = vi.fn(failure === 'pause' ? fail : () => {});
    const remove = vi.fn(failure === 'remove' ? fail : () => {});
    const element = {
      pause,
      remove,
      set srcObject(_value: unknown) {
        if (failure === 'stream') fail();
      },
    } as unknown as HTMLMediaElement;
    const close = vi.fn(() => {
      if (failure === 'close-throw') fail();
      return failure === 'close-reject' ? Promise.reject(new Error('close failed')) : Promise.resolve();
    });
    const audio = new Set([element]);
    const nodes = new Map([[element, { source, analyser }]]);
    expect(() => disposeAudioResources(audio, nodes, { close } as unknown as AudioContext)).not.toThrow();
    await Promise.resolve();
    expect(source.disconnect).toHaveBeenCalledOnce();
    expect(analyser.disconnect).toHaveBeenCalledOnce();
    expect(pause).toHaveBeenCalledOnce();
    expect(remove).toHaveBeenCalledOnce();
    expect(close).toHaveBeenCalledOnce();
    expect(audio.size).toBe(0);
    expect(nodes.size).toBe(0);
  },
);

it('returns synchronously while browser context closure is pending', () => {
  const close = vi.fn(() => new Promise<void>(() => {}));
  expect(disposeAudioResources(new Set(), new Map(), { close } as unknown as AudioContext)).toBeUndefined();
  expect(close).toHaveBeenCalledOnce();
});
