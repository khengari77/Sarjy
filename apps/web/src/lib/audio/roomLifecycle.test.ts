import { expect, it, vi } from 'vitest';
import { RoomEvent, type Room } from 'livekit-client';
import { bindRoomEvents, disconnectRoomInBackground } from './roomLifecycle';

it('removes only app listeners before background teardown and stops the owned microphone', async () => {
  const app = vi.fn(),
    sdk = vi.fn(),
    listeners = new Set([sdk]);
  const stop = vi.fn();
  let reject!: (error: Error) => void;
  const debug = vi.spyOn(console, 'debug').mockImplementation(() => {});
  const unregister = vi.fn();
  const room = {
    on: (_event: unknown, handler: typeof app) => listeners.add(handler),
    off: (_event: unknown, handler: typeof app) => listeners.delete(handler),
    unregisterTextStreamHandler: unregister,
    localParticipant: { getTrackPublication: () => ({ track: { stop } }) },
    disconnect: vi.fn(
      () =>
        new Promise<void>((_resolve, fail) => {
          reject = fail;
        }),
    ),
  };
  try {
    const bindings = bindRoomEvents(room as unknown as Room);
    bindings.on(RoomEvent.Disconnected, app);
    bindings.dispose();
    expect(listeners.has(app)).toBe(false);
    expect(listeners.has(sdk)).toBe(true);
    expect(unregister).toHaveBeenCalledOnce();
    expect(disconnectRoomInBackground(room as unknown as Room)).toBeUndefined();
    expect(stop).toHaveBeenCalledOnce();
    reject(new Error('sensitive provider detail'));
    await Promise.resolve();
    expect(debug).toHaveBeenCalledWith('[Sarjy] Background room disconnect failed');
    expect(JSON.stringify(debug.mock.calls)).not.toContain('sensitive');
  } finally {
    debug.mockRestore();
  }
});

it('handles synchronous disconnect failure without throwing', () => {
  const debug = vi.spyOn(console, 'debug').mockImplementation(() => {});
  try {
    const room = {
      localParticipant: { getTrackPublication: () => undefined },
      disconnect() {
        throw new Error('failed');
      },
    };
    expect(() => disconnectRoomInBackground(room as unknown as Room)).not.toThrow();
  } finally {
    debug.mockRestore();
  }
});
