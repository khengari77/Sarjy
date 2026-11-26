import { describe, expect, it } from 'vitest';
import { applyWordleToolEvent, createActiveSync } from './wordleActive';
import { fakeGame } from './wordleVoiceSupport';

function harness(open: boolean, pending: string | null = null) {
  const all: { type?: unknown }[] = [];
  const state = { open, pending };
  const sync = createActiveSync(
    async (event) => {
      all.push(event);
    },
    () => state.open,
    () => state.pending,
  );
  // Most cases check the popup flag; the pending flag that rides along has its own test.
  const sent = { filter: () => all.filter((event) => event.type === 'wordle.active') };
  return { sent: all, active: sent, state, sync };
}

const status = (text: string, source: 'transport' | 'agent' = 'transport') =>
  ({ type: 'status', source, text, epoch: 1 }) as const;

describe('wordle.active sync', () => {
  it('sends true when the voice room connects with a game already open', () => {
    const { active, sync } = harness(true);
    sync.handleEvent(status('connected'));
    expect(active.filter()).toEqual([{ type: 'wordle.active', value: true }]);
  });

  it('sends false when the game is closed at connect', () => {
    const { active, sync } = harness(false);
    sync.handleEvent(status('connected'));
    expect(active.filter()).toEqual([{ type: 'wordle.active', value: false }]);
  });

  it('re-sends after a reconnect and when the agent joins, but not on disconnect', () => {
    const { sent, active, state, sync } = harness(true);
    sync.handleEvent(status('disconnected'));
    sync.handleEvent(status('reconnecting'));
    expect(sent).toEqual([]);
    state.open = false;
    sync.handleEvent(status('connected'));
    sync.handleEvent(status('listening', 'agent'));
    expect(active.filter()).toEqual([
      { type: 'wordle.active', value: false },
      { type: 'wordle.active', value: false },
    ]);
  });

  it('re-sends the pending yes/no with the popup state so a dropped flag is re-armed', () => {
    const { sent, sync } = harness(true, 'guess');
    sync.handleEvent(status('connected'));
    expect(sent).toEqual([
      { type: 'wordle.active', value: true },
      { type: 'wordle.pending', value: 'guess' },
    ]);
  });

  it('survives a runtime that throws synchronously', () => {
    const sync = createActiveSync(
      () => {
        throw new TypeError('no sendControl');
      },
      () => true,
    );
    expect(() => sync.publish()).not.toThrow();
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
