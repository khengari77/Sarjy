import { createHash, randomUUID } from 'node:crypto';
import { mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import { dirname } from 'node:path';

/**
 * Review-admission counters. Deliberately file-backed: #94 owns the database
 * migration for reviewer access, so this gate must not introduce one.
 *
 * The file is a flat map of `sha256(token)` to `{ used: n }`; the raw token is
 * never written. Reads and writes are serialised per process and each write is
 * a temp file plus `rename`, so concurrent exchanges cannot both consume the
 * last use. A single Node process is assumed (adapter-node demo deployment).
 */
export interface RedeemOutcome {
  ok: boolean;
  remaining: number;
}

export interface AccessStateStore {
  path: string;
  redeem(tokenKey: string, maxUses: number): Promise<RedeemOutcome>;
  uses(tokenKey: string): Promise<number>;
}

interface TokenRecord {
  used: number;
}

type AccessState = Record<string, TokenRecord>;

export function tokenKey(token: string): string {
  return createHash('sha256').update(token, 'utf8').digest('hex');
}

export function createAccessStateStore(options: { path: string; now?: () => number }): AccessStateStore {
  let initialised = false;
  let warnedMissing = false;
  let tail: Promise<unknown> = Promise.resolve();

  function exclusive<T>(task: () => Promise<T>): Promise<T> {
    const run = tail.then(task, task);
    tail = run.then(
      () => undefined,
      () => undefined,
    );
    return run;
  }

  async function read(): Promise<AccessState> {
    try {
      const raw = await readFile(options.path, 'utf8');
      const parsed: unknown = JSON.parse(raw);
      if (!isAccessState(parsed)) throw new Error('invalid state shape');
      initialised = true;
      return parsed;
    } catch (error) {
      const code = (error as NodeJS.ErrnoException).code;
      if (code === 'ENOENT' && !warnedMissing) {
        warnedMissing = true;
        console.warn('review access: state file missing; usage counters start at zero (expiry still applies)');
      } else if (code !== 'ENOENT' && !initialised) {
        console.warn('review access: state file unreadable; usage counters start at zero (expiry still applies)');
      }
      initialised = true;
      return {};
    }
  }

  async function write(state: AccessState): Promise<void> {
    await mkdir(dirname(options.path), { recursive: true });
    const temporary = `${options.path}.${process.pid}.${randomUUID()}.tmp`;
    await writeFile(temporary, `${JSON.stringify(state, null, 2)}\n`, { encoding: 'utf8', mode: 0o600 });
    await rename(temporary, options.path);
  }

  return {
    path: options.path,
    redeem(tokenId, maxUses) {
      return exclusive(async () => {
        const state = await read();
        const record = state[tokenId] ?? { used: 0 };
        if (record.used >= maxUses) return { ok: false, remaining: 0 };
        record.used += 1;
        state[tokenId] = record;
        await write(state);
        return { ok: true, remaining: Math.max(0, maxUses - record.used) };
      });
    },
    uses(tokenId) {
      return exclusive(async () => {
        const state = await read();
        return state[tokenId]?.used ?? 0;
      });
    },
  };
}

function isAccessState(value: unknown): value is AccessState {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  return Object.values(value as Record<string, unknown>).every(
    (record) =>
      !!record &&
      typeof record === 'object' &&
      typeof (record as TokenRecord).used === 'number' &&
      Number.isInteger((record as TokenRecord).used) &&
      (record as TokenRecord).used >= 0,
  );
}
