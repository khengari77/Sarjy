import { describe, expect, it } from 'vitest';
import { createRateLimiter, failureKey } from './rate-limit';

describe('failure cap keys', () => {
  it('separates reviewers behind one shared proxy socket', () => {
    const first = new Request('http://app/', { headers: { 'x-forwarded-for': '203.0.113.7, 10.0.0.1' } });
    const second = new Request('http://app/', { headers: { 'x-forwarded-for': '198.51.100.9' } });
    expect(failureKey(first, '10.0.0.1')).not.toBe(failureKey(second, '10.0.0.1'));
  });

  it('falls back to the socket address when no forwarded header exists', () => {
    expect(failureKey(new Request('http://app/'), '10.0.0.1')).toBe('10.0.0.1');
  });
});

describe('rate limiter', () => {
  it('blocks only the failing key and resets after the window', () => {
    let now = 0;
    const limiter = createRateLimiter({ limit: 2, windowMs: 1_000, now: () => now });
    for (let attempt = 0; attempt < 2; attempt += 1) limiter.fail('a');
    expect(limiter.blocked('a')).toBe(true);
    expect(limiter.blocked('b')).toBe(false);
    now = 1_000;
    expect(limiter.blocked('a')).toBe(false);
  });
});
