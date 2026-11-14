import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createAccessStateStore, tokenKey } from './access-state';
import { redeemAccessToken } from './access';

const TOKEN = 'concurrency-token-value';
const KEY = tokenKey(TOKEN);
const NOW = 1_700_000_000_000;

const directories: string[] = [];

afterEach(async () => {
  vi.restoreAllMocks();
  await Promise.all(directories.splice(0).map((directory) => rm(directory, { recursive: true, force: true })));
});

async function statePath(): Promise<string> {
  const directory = await mkdtemp(join(tmpdir(), 'sarjy-access-'));
  directories.push(directory);
  return join(directory, 'state.json');
}

describe('review access accounting', () => {
  it('allows exactly N exchanges and refuses N+1 without a cookie', async () => {
    const store = createAccessStateStore({ path: await statePath() });
    const config = { token: TOKEN, expiresAt: NOW + 60_000, maxUses: 3 };
    const results = [];
    for (let attempt = 0; attempt < 4; attempt += 1) {
      results.push(await redeemAccessToken({ candidate: TOKEN, config, store, now: NOW }));
    }
    expect(results.map((result) => result.ok)).toEqual([true, true, true, false]);
    expect(await store.uses(KEY)).toBe(3);
  });

  it('lets exactly one exchange take the last use under concurrency', async () => {
    const store = createAccessStateStore({ path: await statePath() });
    const config = { token: TOKEN, expiresAt: NOW + 60_000, maxUses: 3 };
    await redeemAccessToken({ candidate: TOKEN, config, store, now: NOW });
    await redeemAccessToken({ candidate: TOKEN, config, store, now: NOW });
    const outcomes = await Promise.all(
      Array.from({ length: 10 }, () => redeemAccessToken({ candidate: TOKEN, config, store, now: NOW })),
    );
    expect(outcomes.filter((outcome) => outcome.ok)).toHaveLength(1);
    expect(await store.uses(KEY)).toBe(3);
  });

  it('keeps counters across store instances when the file persists', async () => {
    const path = await statePath();
    const config = { token: TOKEN, expiresAt: NOW + 60_000, maxUses: 2 };
    await redeemAccessToken({ candidate: TOKEN, config, store: createAccessStateStore({ path }), now: NOW });
    const restarted = createAccessStateStore({ path });
    expect(await restarted.uses(KEY)).toBe(1);
    expect((await redeemAccessToken({ candidate: TOKEN, config, store: restarted, now: NOW })).ok).toBe(true);
    const file = JSON.parse(await readFile(path, 'utf8')) as Record<string, { used: number }>;
    expect(file[KEY]).toEqual({ used: 2 });
    expect(JSON.stringify(file)).not.toContain(TOKEN);
  });

  it('fails closed on a corrupt state file instead of treating it as empty', async () => {
    const warn = vi.spyOn(console, 'error').mockImplementation(() => {});
    const path = await statePath();
    await writeFile(path, '{not json', 'utf8');
    const store = createAccessStateStore({ path });
    const result = await redeemAccessToken({
      candidate: TOKEN,
      config: { token: TOKEN, expiresAt: NOW + 60_000, maxUses: 5 },
      store,
      now: NOW,
    });
    expect(result).toEqual({ ok: false, reason: 'unavailable' });
    expect(warn).toHaveBeenCalledWith(expect.stringContaining('fail closed'));
  });

  it('starts from zero and says so when the state file is missing', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const path = await statePath();
    const store = createAccessStateStore({ path });
    expect(await store.uses(KEY)).toBe(0);
    expect(warn).toHaveBeenCalledWith(expect.stringContaining('usage counters start at zero'));
  });

  it('rejects an expired token before touching the counter', async () => {
    const store = createAccessStateStore({ path: await statePath() });
    const config = { token: TOKEN, expiresAt: NOW - 1, maxUses: 5 };
    const result = await redeemAccessToken({ candidate: TOKEN, config, store, now: NOW });
    expect(result).toEqual({ ok: false, reason: 'expired' });
    expect(await store.uses(KEY)).toBe(0);
  });

  it('rejects a wrong token and does not touch another token counter', async () => {
    const store = createAccessStateStore({ path: await statePath() });
    const config = { token: TOKEN, expiresAt: NOW + 60_000, maxUses: 5 };
    expect((await redeemAccessToken({ candidate: 'wrong', config, store, now: NOW })).ok).toBe(false);
    expect(await store.uses(KEY)).toBe(0);
  });
});
