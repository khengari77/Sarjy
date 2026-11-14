import { afterEach, expect, it, vi } from 'vitest';
import { request } from './api';

afterEach(() => vi.unstubAllGlobals());

it.each([
  [404, '', 'HTTP_404'],
  [405, '', 'HTTP_405'],
  [401, 'PRIVATE plain text', 'HTTP_401'],
  [502, '{"detail":{"code":42}}', 'HTTP_502'],
  [400, '{"detail":{"code":"INVALID_INPUT"}}', 'INVALID_INPUT'],
  [200, 'PRIVATE malformed', 'API_RESPONSE_INVALID'],
])('preserves safe errors for status %s', async (status, body, code) => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(body, { status })));
  await expect(request('conversations')).rejects.toThrow(code);
});

it('returns a successful JSON response', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ id: 'ok' })));
  await expect(request('conversations')).resolves.toEqual({ id: 'ok' });
});
