import { describe, expect, it, vi } from 'vitest';
import {
  MODE_STORAGE_KEY,
  createModeController,
  initialDevModeState,
  readStoredMode,
  resolveStoredMode,
  writeStoredMode,
  type DevAccess,
  type ModeStorage,
} from './mode';

function memoryStorage(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => void data.set(key, value),
    read: (key: string) => data.get(key) ?? null,
    data,
  } satisfies ModeStorage & { read(key: string): string | null; data: Map<string, string> };
}

function controller(access: DevAccess | Error, storage: ModeStorage = memoryStorage()) {
  const authorize = vi.fn(async () => {
    if (access instanceof Error) throw access;
    return access;
  });
  const state = initialDevModeState();
  return { state, authorize, storage, actions: createModeController(state, { getStorage: () => storage, authorize }) };
}

describe('developer mode gating', () => {
  it('only accepts the literal developer value from storage', () => {
    expect(resolveStoredMode('developer')).toBe('developer');
    expect(resolveStoredMode('DEVELOPER')).toBe('simple');
    expect(resolveStoredMode('true')).toBe('simple');
    expect(resolveStoredMode(null)).toBe('simple');
    expect(resolveStoredMode('')).toBe('simple');
  });

  it('starts in Simple mode and never assumes access', () => {
    expect(initialDevModeState()).toEqual({ mode: 'simple', authorization: 'checking', error: '' });
  });

  it('restores Developer mode only when the server check authorizes it', async () => {
    const storage = memoryStorage({ [MODE_STORAGE_KEY]: 'developer' });
    const { state, authorize, actions } = controller('authorized', storage);
    expect(await actions.hydrate()).toBe('developer');
    expect(state.mode).toBe('developer');
    expect(authorize).toHaveBeenCalledTimes(1);
    expect(storage.read(MODE_STORAGE_KEY)).toBe('developer');
  });

  it('falls back to Simple and clears the preference when the check is denied', async () => {
    const storage = memoryStorage({ [MODE_STORAGE_KEY]: 'developer' });
    const { state, actions } = controller('unauthorized', storage);
    expect(await actions.hydrate()).toBe('simple');
    expect(state).toMatchObject({ mode: 'simple', authorization: 'unauthorized', error: 'DEV_MODE_UNAUTHORIZED' });
    expect(storage.read(MODE_STORAGE_KEY)).toBe('simple');
  });

  it('keeps a stored preference during a transient outage but stays in Simple mode', async () => {
    const storage = memoryStorage({ [MODE_STORAGE_KEY]: 'developer' });
    const { state, actions } = controller('unavailable', storage);
    expect(await actions.hydrate()).toBe('simple');
    expect(state).toMatchObject({ mode: 'simple', authorization: 'unavailable', error: 'DEV_MODE_UNAVAILABLE' });
    expect(storage.read(MODE_STORAGE_KEY)).toBe('developer');
  });

  it('toggles into Developer only after authorization and persists it', async () => {
    const storage = memoryStorage();
    const { state, authorize, actions } = controller('authorized', storage);
    await actions.hydrate();
    expect(state.authorization).toBe('authorized');
    expect(await actions.toggle()).toBe(true);
    expect(state.mode).toBe('developer');
    expect(storage.read(MODE_STORAGE_KEY)).toBe('developer');
    expect(authorize).toHaveBeenCalledTimes(2);
  });

  it('leaving Developer mode is local, immediate and makes no server call', async () => {
    const storage = memoryStorage({ [MODE_STORAGE_KEY]: 'developer' });
    const { state, authorize, actions } = controller('authorized', storage);
    await actions.hydrate();
    const before = authorize.mock.calls.length;
    expect(await actions.setMode('simple')).toBe(true);
    expect(state.mode).toBe('simple');
    expect(authorize).toHaveBeenCalledTimes(before);
    expect(storage.read(MODE_STORAGE_KEY)).toBe('simple');
  });

  it('does not enter Developer mode when authorization throws', async () => {
    const { state, actions } = controller(new Error('offline'));
    expect(await actions.toggle()).toBe(false);
    expect(state).toMatchObject({ mode: 'simple', authorization: 'unavailable', error: 'DEV_MODE_UNAVAILABLE' });
  });

  it('a rejected diagnostics read demotes an open Developer view', async () => {
    const storage = memoryStorage({ [MODE_STORAGE_KEY]: 'developer' });
    const { state, actions } = controller('authorized', storage);
    await actions.hydrate();
    expect(state.mode).toBe('developer');
    actions.requireSimple('DEV_CONTEXT_UNAUTHORIZED');
    expect(state).toMatchObject({ mode: 'simple', authorization: 'unauthorized', error: 'DEV_CONTEXT_UNAUTHORIZED' });
    expect(storage.read(MODE_STORAGE_KEY)).toBe('simple');
  });

  it('survives unavailable or throwing local storage', () => {
    expect(readStoredMode(null)).toBe('simple');
    expect(writeStoredMode(null, 'developer')).toBe(false);
    const throwing = {
      getItem: () => {
        throw new Error('blocked');
      },
      setItem: () => {
        throw new Error('blocked');
      },
    };
    expect(readStoredMode(throwing)).toBe('simple');
    expect(writeStoredMode(throwing, 'developer')).toBe(false);
  });
});
