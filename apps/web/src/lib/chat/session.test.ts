import { afterEach, describe, expect, it, vi } from 'vitest';
import { mergeVoices, startVoiceSession } from './session';

const voice = (id: string, name: string) => ({ id, name, languages: [] });

describe('mergeVoices', () => {
  it('appends a new page without dropping earlier voices', () => {
    expect(mergeVoices([voice('a', 'A')], [voice('b', 'B')])).toEqual([voice('a', 'A'), voice('b', 'B')]);
  });

  it('lets the later page replace a duplicate id in place', () => {
    expect(mergeVoices([voice('a', 'Old')], [voice('a', 'New')])).toEqual([voice('a', 'New')]);
  });
});

describe('startVoiceSession', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('omits voice_id so the server can apply its default', async () => {
    const fetch = vi.fn().mockResolvedValue(Response.json({ id: 'session' }));
    vi.stubGlobal('fetch', fetch);
    await startVoiceSession('chat-1', 'en');
    const body = JSON.parse(fetch.mock.calls[0][1].body);
    expect(body).toEqual({ conversation_id: 'chat-1', language: 'en', response_preference: 'automatic' });
  });

  it('sends an explicitly selected approved voice', async () => {
    const fetch = vi.fn().mockResolvedValue(Response.json({ id: 'session' }));
    vi.stubGlobal('fetch', fetch);
    await startVoiceSession('chat-1', 'ar', 'voice-1');
    expect(JSON.parse(fetch.mock.calls[0][1].body).voice_id).toBe('voice-1');
  });
});
