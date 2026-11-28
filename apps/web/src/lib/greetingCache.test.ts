import { describe, expect, it } from 'vitest';
import { applyGreetingCacheHeaders, GREETING_CACHE_CONTROL } from './greetingCache';

describe('greeting clip cache headers', () => {
  it('allows a day of shared caching for greeting clips only', () => {
    const response = applyGreetingCacheHeaders('/greetings/voice/full.mp3', new Response('clip'));
    expect(response.headers.get('cache-control')).toBe(GREETING_CACHE_CONTROL);
    expect(GREETING_CACHE_CONTROL).toBe('private, max-age=86400');
  });

  it('leaves every other response untouched', () => {
    for (const pathname of ['/', '/greetings', '/greetings-other/voice/full.mp3', '/api/v1/settings']) {
      const response = applyGreetingCacheHeaders(pathname, new Response('page'));
      expect(response.headers.get('cache-control')).toBeNull();
    }
  });
});
