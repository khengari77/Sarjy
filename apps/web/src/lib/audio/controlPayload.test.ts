import { describe, expect, it } from 'vitest';
import { controlEvent } from './controlPayload';

const encode = (value: unknown) => new TextEncoder().encode(JSON.stringify(value));

describe('room control payloads', () => {
  it('parses a resync request without inventing a command', () => {
    expect(controlEvent('sarjy.command', encode({ resync: true }))).toEqual({ type: 'resync' });
  });

  it('still parses a full command result', () => {
    const event = controlEvent(
      'sarjy.command',
      encode({
        request_id: 'command:turn',
        kind: 'executed',
        action: 'SetCaptionsVisible',
        settings: {
          speaking_speed: 1,
          captions_visible: false,
          approved_voice_id: null,
          response_preference: 'automatic',
          revision: 1,
        },
        navigation: null,
        theme: null,
        changed: true,
        spoken: 'Captions hidden.',
        detail: null,
      }),
    );
    expect(event).toMatchObject({ type: 'command', command: { action: 'SetCaptionsVisible' } });
  });

  it('drops malformed or unrelated payloads', () => {
    expect(controlEvent('sarjy.command', encode({ nope: 1 }))).toBeNull();
    expect(controlEvent('sarjy.command', new TextEncoder().encode('not json'))).toBeNull();
    expect(controlEvent('sarjy.error', encode({ resync: true }))).toBeNull();
  });
});

describe('sarjy.wordle control events', () => {
  const bytes = (value: unknown) => new TextEncoder().encode(JSON.stringify(value));

  it('accepts only the three signal actions', () => {
    expect(controlEvent('sarjy.wordle', bytes({ action: 'open' }))).toEqual({ type: 'wordle', action: 'open' });
    expect(controlEvent('sarjy.wordle', bytes({ action: 'refresh' }))).toEqual({ type: 'wordle', action: 'refresh' });
    expect(controlEvent('sarjy.wordle', bytes({ action: 'close' }))).toEqual({ type: 'wordle', action: 'close' });
    expect(controlEvent('sarjy.wordle', bytes({ action: 'reveal' }))).toBeNull();
    expect(controlEvent('sarjy.wordle', bytes([]))).toBeNull();
  });
});
