import { afterEach, describe, expect, it, vi } from 'vitest';
import { createCallTimer } from './callTiming';

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('call-start timer', () => {
  it('records marks relative to press and emits one report with per-step deltas', () => {
    vi.stubEnv('DEV', true);
    const info = vi.spyOn(console, 'info').mockImplementation(() => {});
    let now = 100;
    const timer = createCallTimer(() => now);

    timer.mark('press');
    now = 150;
    timer.mark('conversation_ready');
    now = 200;
    timer.mark('mic_ready');

    const report = timer.report();
    expect(report?.marks).toEqual({ press: 0, conversation_ready: 50, mic_ready: 100 });
    expect(report?.steps).toEqual({ press: 0, conversation_ready: 50, mic_ready: 50 });
    expect(info).toHaveBeenCalledExactlyOnceWith('[Sarjy call-start]', {
      press: 0,
      conversation_ready: 50,
      mic_ready: 100,
      steps: { press: 0, conversation_ready: 50, mic_ready: 50 },
    });
    expect(timer.report()).toBeNull();
    expect(info).toHaveBeenCalledTimes(1);
  });

  it('keeps the first instant for a repeated mark and ignores marks after the report', () => {
    vi.stubEnv('DEV', true);
    vi.spyOn(console, 'info').mockImplementation(() => {});
    let now = 10;
    const timer = createCallTimer(() => now);

    timer.mark('press');
    now = 50;
    timer.mark('agent_state_first');
    now = 90;
    timer.mark('agent_state_first');
    timer.report();
    timer.mark('first_agent_audio');

    expect(timer.report()).toBeNull();
    expect(vi.mocked(console.info).mock.calls[0][1]).toEqual({
      press: 0,
      agent_state_first: 40,
      steps: { press: 0, agent_state_first: 40 },
    });
  });

  it('does nothing when timing is disabled', () => {
    vi.stubEnv('DEV', false);
    const info = vi.spyOn(console, 'info').mockImplementation(() => {});
    const timer = createCallTimer(() => 10);

    expect(timer.enabled).toBe(false);
    timer.mark('press');
    expect(timer.report()).toBeNull();
    expect(info).not.toHaveBeenCalled();
  });

  it('is enabled in production by the localStorage flag and keeps the report on window', () => {
    vi.stubEnv('DEV', false);
    const stored = new Map([['sarjy:call-timing', '1']]);
    const window = {
      localStorage: { getItem: (key: string) => stored.get(key) ?? null },
      __sarjyCallStart: undefined as unknown,
    };
    vi.stubGlobal('window', window);
    vi.spyOn(console, 'info').mockImplementation(() => {});
    let now = 0;
    const timer = createCallTimer(() => now);

    expect(timer.enabled).toBe(true);
    timer.mark('press');
    now = 25;
    timer.mark('session_response');
    timer.report();

    expect(window.__sarjyCallStart).toEqual({
      press: 0,
      session_response: 25,
      steps: { press: 0, session_response: 25 },
    });
  });
});
