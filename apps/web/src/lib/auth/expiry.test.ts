import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ACCESS_EXPIRY_ERROR, resolveAccessExpiry } from './expiry';

const INSTANT = '2026-12-31T23:59:59Z';

describe('review access expiry', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('resolves the same absolute instant on two consecutive boots', () => {
    const first = resolveAccessExpiry(INSTANT);
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'));
    const second = resolveAccessExpiry(INSTANT);
    vi.setSystemTime(new Date('2026-06-01T00:00:00Z'));
    const third = resolveAccessExpiry(INSTANT);
    expect(first).toBe(Date.parse(INSTANT));
    expect(second).toBe(first);
    expect(third).toBe(first);
  });

  it('does not move the deadline forward with the clock', () => {
    const bootOne = resolveAccessExpiry(INSTANT);
    vi.setSystemTime(new Date('2026-01-02T00:00:00Z'));
    const bootTwo = resolveAccessExpiry(INSTANT);
    expect(bootTwo - bootOne).toBe(0);
  });

  it.each([undefined, null, '', 'tomorrow', '2026-13-40T00:00:00Z'])('fails closed for %s', (value) => {
    expect(() => resolveAccessExpiry(value)).toThrow(ACCESS_EXPIRY_ERROR);
  });
});
