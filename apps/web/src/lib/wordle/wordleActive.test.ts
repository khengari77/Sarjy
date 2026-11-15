import { describe, expect, it } from 'vitest';
import { applyWordleToolEvent, createActiveSync } from './wordleActive';
import { fakeGame } from './wordleVoiceSupport';

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

describe('model tool events', () => {
  const event = (action: 'open' | 'refresh' | 'close') => ({ type: 'wordle', action, epoch: 1 }) as const;

  it('opens a closed popup and refreshes the board in place', () => {
    const fake = fakeGame({ open: false });
    let refreshed = 0;
    fake.port.refresh = async () => {
      refreshed += 1;
    };
    applyWordleToolEvent(fake.port, event('open'));
    expect(fake.calls.open).toBe(1);
    expect(refreshed).toBe(1);
    applyWordleToolEvent(fake.port, event('open'));
    expect(fake.calls.open).toBe(1);
    expect(refreshed).toBe(2);
  });

  it('closes on close and ignores unrelated events', () => {
    const fake = fakeGame({ open: true });
    applyWordleToolEvent(fake.port, event('close'));
    expect(fake.state.open).toBe(false);
    applyWordleToolEvent(fake.port, { type: 'resync', epoch: 1 });
    expect(fake.calls.open).toBe(0);
  });
});
