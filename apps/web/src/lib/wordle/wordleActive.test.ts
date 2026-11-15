import { describe, expect, it } from 'vitest';
import { createActiveSync } from './wordleActive';

function harness(open: boolean) {
  const sent: unknown[] = [];
  const state = { open };
  const sync = createActiveSync(
    async (event) => {
      sent.push(event);
    },
    () => state.open,
  );
  return { sent, state, sync };
}

const status = (text: string, source: 'transport' | 'agent' = 'transport') =>
  ({ type: 'status', source, text, epoch: 1 }) as const;

describe('wordle.active sync', () => {
  it('sends true when the voice room connects with a game already open', () => {
    const { sent, sync } = harness(true);
    sync.handleEvent(status('connected'));
    expect(sent).toEqual([{ type: 'wordle.active', value: true }]);
  });

  it('sends false when the game is closed at connect', () => {
    const { sent, sync } = harness(false);
    sync.handleEvent(status('connected'));
    expect(sent).toEqual([{ type: 'wordle.active', value: false }]);
  });

  it('re-sends after a reconnect and when the agent joins, but not on disconnect', () => {
    const { sent, state, sync } = harness(true);
    sync.handleEvent(status('disconnected'));
    sync.handleEvent(status('reconnecting'));
    expect(sent).toEqual([]);
    state.open = false;
    sync.handleEvent(status('connected'));
    sync.handleEvent(status('listening', 'agent'));
    expect(sent).toEqual([
      { type: 'wordle.active', value: false },
      { type: 'wordle.active', value: false },
    ]);
  });

  it('ignores transcripts', () => {
    const { sent, sync } = harness(true);
    sync.handleEvent({ type: 'transcript', text: 'hi', final: true, segmentId: 's', speaker: 'user', epoch: 1 });
    expect(sent).toEqual([]);
  });
});
