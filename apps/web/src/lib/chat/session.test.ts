import { afterEach, describe, expect, it, vi } from 'vitest';
import { request } from '../api';
import { mergeVoices, startVoiceSession, undoChange } from './session';

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

  it('never sends a voice id, even when the picker has a saved value', async () => {
    const fetch = vi.fn().mockResolvedValue(Response.json({ id: 'session' }));
    vi.stubGlobal('fetch', fetch);
    await startVoiceSession('chat-1', 'ar');
    expect(JSON.parse(fetch.mock.calls[0][1].body).voice_id).toBeUndefined();
  });
});

describe('undoChange', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('treats a 204 as a successful change', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
    await expect(undoChange('change-1')).resolves.toEqual({ changed: true });
  });

  it('treats an empty 2xx body as a successful change for undo', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 200 })));
    await expect(undoChange('change-1')).resolves.toEqual({ changed: true });
  });

  it('still requires JSON from other requests', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 200 })));
    await expect(request('conversations')).rejects.toThrow('API_RESPONSE_INVALID');
  });

  it('preserves the HTTP status for a plain 404', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 404 })));
    await expect(undoChange('change-1')).rejects.toThrow('HTTP_404');
  });
});
