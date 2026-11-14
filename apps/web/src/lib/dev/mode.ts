/**
 * Simple / Developer mode selection.
 *
 * The choice is cosmetic and stored locally. Entering Developer mode is gated
 * by a server check (a read-only context GET, see `contextApi.ts`); the stored
 * preference alone never grants access. Toggling this module performs no voice
 * session work: it never POSTs `voice/sessions`, dispatches an agent, or touches
 * the orb.
 */

export type UiMode = 'simple' | 'developer';

/** Result of the server-side developer-diagnostics check. */
export type DevAccess = 'authorized' | 'unauthorized' | 'unavailable';
export type DevAuthorization = 'checking' | DevAccess;

export interface DevModeState {
  mode: UiMode;
  authorization: DevAuthorization;
  /** Stable error code; empty when there is nothing to report. */
  error: string;
}

export const MODE_STORAGE_KEY = 'sarjy-ui-mode';

export type ModeStorage = Pick<Storage, 'getItem' | 'setItem'>;

export function initialDevModeState(): DevModeState {
  return { mode: 'simple', authorization: 'checking', error: '' };
}

/** Only the literal `developer` opts in; anything else is Simple mode. */
export function resolveStoredMode(value: string | null | undefined): UiMode {
  return value === 'developer' ? 'developer' : 'simple';
}

export function readStoredMode(storage: ModeStorage | null): UiMode {
  if (!storage) return 'simple';
  try {
    return resolveStoredMode(storage.getItem(MODE_STORAGE_KEY));
  } catch {
    return 'simple';
  }
}

export function writeStoredMode(storage: ModeStorage | null, mode: UiMode): boolean {
  if (!storage) return false;
  try {
    storage.setItem(MODE_STORAGE_KEY, mode);
    return true;
  } catch {
    return false;
  }
}

export function authorizationError(access: DevAccess): string {
  if (access === 'unauthorized') return 'DEV_MODE_UNAUTHORIZED';
  if (access === 'unavailable') return 'DEV_MODE_UNAVAILABLE';
  return '';
}

export interface ModeControllerDeps {
  getStorage(): ModeStorage | null;
  /** Read-only server check; must never create a voice session. */
  authorize(): Promise<DevAccess>;
}

export function createModeController(state: DevModeState, deps: ModeControllerDeps) {
  let generation = 0;

  function demote(access: DevAccess) {
    state.mode = 'simple';
    state.authorization = access;
    state.error = authorizationError(access);
    // A denied check clears the cosmetic preference; a transient outage keeps
    // it so the next successful load can restore Developer mode.
    if (access === 'unauthorized') writeStoredMode(deps.getStorage(), 'simple');
  }

  async function checkAuthorization(): Promise<DevAccess> {
    const current = ++generation;
    state.authorization = 'checking';
    let access: DevAccess;
    try {
      access = await deps.authorize();
    } catch {
      access = 'unavailable';
    }
    if (current !== generation) return 'unavailable';
    if (access === 'authorized') {
      state.authorization = 'authorized';
      state.error = '';
    } else {
      demote(access);
    }
    return access;
  }

  /** Restore the stored preference, then verify access before honoring it. */
  async function hydrate(): Promise<UiMode> {
    const stored = readStoredMode(deps.getStorage());
    const access = await checkAuthorization();
    if (stored === 'developer' && access === 'authorized') {
      state.mode = 'developer';
      state.error = '';
    }
    return state.mode;
  }

  /** Reversible: leaving Developer mode is local and never calls the server. */
  async function setMode(next: UiMode): Promise<boolean> {
    if (next === 'simple') {
      generation += 1;
      state.mode = 'simple';
      state.error = '';
      writeStoredMode(deps.getStorage(), 'simple');
      return true;
    }
    const access = await checkAuthorization();
    if (access !== 'authorized') return false;
    state.mode = 'developer';
    state.error = '';
    writeStoredMode(deps.getStorage(), 'developer');
    return true;
  }

  async function toggle(): Promise<boolean> {
    return setMode(state.mode === 'developer' ? 'simple' : 'developer');
  }

  /** Called when a diagnostics read is rejected mid-view. */
  function requireSimple(error: string) {
    generation += 1;
    demote('unauthorized');
    state.error = error;
  }

  return { hydrate, setMode, toggle, requireSimple };
}

export type ModeController = ReturnType<typeof createModeController>;
