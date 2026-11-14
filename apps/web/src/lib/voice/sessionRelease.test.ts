import { afterEach, expect, it, vi } from 'vitest';
import type { SessionConfig } from '../api';

const mocks = vi.hoisted(() => ({ end: vi.fn(async () => {}) }));

vi.mock('../chat/session', () => ({
  startVoiceSession: vi.fn(),
  endVoiceSession: mocks.end,
}));

const { createSessionReleaser } = await import('./sessionRelease');

const session: SessionConfig = {
  id: 'sess-1',
  url: 'ws://fake.invalid',
  token: 'token',
  expires_at: new Date(Date.now() + 60000).toISOString(),
};

afterEach(() => {
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

it('reports a failed release with the session id and failure name', async () => {
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  mocks.end.mockRejectedValueOnce(new Error('503'));

  await expect(createSessionReleaser()(session)).resolves.toBe(false);

  expect(warn).toHaveBeenCalledWith('[Sarjy] Voice session release failed', {
    sessionId: 'sess-1',
    reason: 'Error',
  });
});

it('marks a session released only after the backend acknowledged it', async () => {
  const release = createSessionReleaser();
  expect(await release(session)).toBe(true);
  expect(await release(session)).toBe(true);
  expect(mocks.end).toHaveBeenCalledOnce();
});

it('retries a failed release instead of treating it as released', async () => {
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  mocks.end.mockRejectedValueOnce(new Error('503'));

  const release = createSessionReleaser();
  expect(await release(session)).toBe(false);
  expect(await release(session)).toBe(true);
  expect(mocks.end).toHaveBeenCalledTimes(2);
  expect(warn).toHaveBeenCalledOnce();
});

it('shares one in-flight attempt for concurrent releases', async () => {
  const release = createSessionReleaser();
  const results = await Promise.all([release(session), release(session)]);
  expect(results).toEqual([true, true]);
  expect(mocks.end).toHaveBeenCalledOnce();
});

it('bounds remembered releases so a long-lived tab does not leak session ids', async () => {
  const release = createSessionReleaser();
  for (let index = 0; index < 33; index += 1) {
    expect(await release({ ...session, id: `sess-${index}` })).toBe(true);
  }
  expect(mocks.end).toHaveBeenCalledTimes(33);

  // The oldest id was forgotten, so it is released again...
  await release({ ...session, id: 'sess-0' });
  expect(mocks.end).toHaveBeenCalledTimes(34);
  // ...while the newest is still remembered.
  await release({ ...session, id: 'sess-32' });
  expect(mocks.end).toHaveBeenCalledTimes(34);
});
