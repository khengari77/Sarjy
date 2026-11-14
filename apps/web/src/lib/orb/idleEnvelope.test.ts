import { describe, expect, it } from 'vitest';
import { idleEnvelope, STILL_ENVELOPE } from './idleEnvelope';

function samples(count = 400, step = 0.13): number[] {
  return Array.from({ length: count }, (_, index) => index * step);
}

describe('idle envelope', () => {
  it('moves with time without any audio input', () => {
    const first = idleEnvelope(0);
    const later = idleEnvelope(2.4);
    expect(later).not.toEqual(first);
    const breaths = samples().map((time) => idleEnvelope(time).breath);
    expect(Math.max(...breaths) - Math.min(...breaths)).toBeGreaterThan(0.5);
  });

  it('stays inside 0..1 for every component', () => {
    for (const time of samples(1200, 0.37)) {
      const envelope = idleEnvelope(time);
      for (const value of [envelope.breath, envelope.flow, envelope.pulse]) {
        expect(Number.isFinite(value)).toBe(true);
        expect(value).toBeGreaterThanOrEqual(0);
        expect(value).toBeLessThanOrEqual(1);
      }
    }
  });

  it('is deterministic for the same time', () => {
    expect(idleEnvelope(4.2)).toEqual(idleEnvelope(4.2));
  });

  it('maps non-finite and negative times to the still start', () => {
    expect(idleEnvelope(Number.NaN)).toEqual(idleEnvelope(0));
    expect(idleEnvelope(Number.POSITIVE_INFINITY)).toEqual(idleEnvelope(0));
    expect(idleEnvelope(-9)).toEqual(idleEnvelope(0));
  });

  it('returns a still envelope under reduced motion', () => {
    for (const time of samples(50, 1.7)) {
      expect(idleEnvelope(time, true)).toEqual(STILL_ENVELOPE);
    }
    expect(STILL_ENVELOPE).toEqual({ breath: 0, flow: 0, pulse: 0 });
  });
});
