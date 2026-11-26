import { describe, expect, it, vi } from 'vitest';
import type { Room } from 'livekit-client';
import { publishControlFlag } from './agentRpc';

function fakeRoom(state: string) {
  const publishData = vi.fn().mockResolvedValue(undefined);
  return { room: { state, localParticipant: { publishData } } as unknown as Room, publishData };
}

describe('publishControlFlag', () => {
  it('waits for a connected room', async () => {
    // Publishing while the room was still connecting broke every later data publish in livekit-client.
    for (const state of ['connecting', 'reconnecting', 'disconnected']) {
      const { room, publishData } = fakeRoom(state);
      await publishControlFlag(room, { type: 'wordle.active', value: true });
      expect(publishData).not.toHaveBeenCalled();
    }
    const { room, publishData } = fakeRoom('connected');
    await publishControlFlag(room, { type: 'wordle.reveal_pending', value: true });
    expect(publishData).toHaveBeenCalledOnce();
    const [data, options] = publishData.mock.calls[0];
    expect(JSON.parse(new TextDecoder().decode(data))).toEqual({ type: 'wordle.reveal_pending', value: true });
    expect(options).toEqual({ reliable: true, topic: 'sarjy.control' });
  });

  it('drops a flag when there is no room', async () => {
    await expect(publishControlFlag(null, { type: 'x', value: false })).resolves.toBeUndefined();
  });
});
