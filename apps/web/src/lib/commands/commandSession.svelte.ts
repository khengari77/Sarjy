/** One reactive command session: the GUI and worker results share this state. */

import {
  fetchSettings,
  saveCaptionSelection,
  saveMemoryMode,
  saveSpeakingSpeed,
  undoChange as undoChangeRequest,
} from '../chat/session';
import type { CatalogState } from '../voice/catalog';
import type { ThemePreference } from '../ui/theme';
import {
  createCommandStore,
  initialCommandState,
  type CommandResult,
  type MemoryChange,
  type MemoryControl,
  type MemoryMode,
} from './commandStore';

export interface CommandSessionOptions {
  /**
   * Applies a browser-local theme through the shell's theme session. The
   * server never stores a theme, so this is the only effect for `SetTheme`.
   */
  setTheme?: (preference: ThemePreference) => void;
  /** Bumps the memory panel's refresh signal after an undo restores a fact. */
  onMemoryChange?: () => void;
}

const REVISION_CONFLICT = 'SETTINGS_REVISION_CONFLICT';

/** Write the mode; on a revision conflict adopt the newest revision via `adopt`, then retry once. */
async function saveMemoryModeWithRetry(mode: MemoryMode, revision: number, adopt: () => Promise<number>) {
  try {
    return await saveMemoryMode(mode, revision);
  } catch (error) {
    if (!(error instanceof Error && error.message === REVISION_CONFLICT)) throw error;
    return saveMemoryMode(mode, await adopt());
  }
}

function memoryFailureText(detail: string): string {
  return detail === REVISION_CONFLICT
    ? 'Settings changed in another window. Try again.'
    : "Couldn't change memory mode. Nothing was changed.";
}

function changeFailureText(detail: string): string {
  if (detail === 'CHANGE_SUPERSEDED') return 'That memory changed since, so I left it alone.';
  if (detail === 'CHANGE_NOT_FOUND') return 'That change is no longer available.';
  return "Couldn't undo that change. Nothing was changed.";
}

type ApplyResult = (result: CommandResult) => boolean;
type RecordFailure = (action: string, requestId: string, detail: string) => void;

/**
 * Captions are server-owned: send the canonical settings action and apply the
 * authoritative snapshot. A rejected write changes nothing.
 */
async function writeCaptions(
  visible: boolean,
  revision: number,
  apply: ApplyResult,
  fail: RecordFailure,
): Promise<boolean> {
  const requestId = `captions:${revision}:${visible ? 'on' : 'off'}`;
  try {
    const result = await saveCaptionSelection(visible, revision);
    return apply({
      request_id: requestId,
      kind: result.changed ? 'executed' : 'unchanged',
      action: 'SetCaptionsVisible',
      settings: result.settings,
      navigation: null,
      theme: null,
      changed: result.changed,
      spoken: result.settings.captions_visible
        ? result.changed
          ? 'Captions shown.'
          : 'Captions are already on.'
        : result.changed
          ? 'Captions hidden.'
          : 'Captions are already hidden.',
      detail: null,
    });
  } catch (error) {
    fail('SetCaptionsVisible', requestId, error instanceof Error ? error.message : 'SETTING_REJECTED');
    return false;
  }
}

/**
 * Speed is a server-owned preference read by the worker at the next turn.
 * A rejected write leaves the previous snapshot in place.
 */
async function writeSpeakingSpeed(
  speed: number,
  revision: number,
  apply: ApplyResult,
  fail: RecordFailure,
): Promise<boolean> {
  const requestId = `speed:${revision}:${speed.toFixed(1)}`;
  try {
    const result = await saveSpeakingSpeed(speed, revision);
    return apply({
      request_id: requestId,
      kind: result.changed ? 'executed' : 'unchanged',
      action: 'SetSpeakingSpeed',
      settings: result.settings,
      navigation: null,
      theme: null,
      changed: result.changed,
      spoken: result.changed
        ? `Speaking speed set to ${result.settings.speaking_speed.toFixed(1)} for the next turn.`
        : `Speaking speed is already ${result.settings.speaking_speed.toFixed(1)}.`,
      detail: null,
    });
  } catch (error) {
    fail('SetSpeakingSpeed', requestId, error instanceof Error ? error.message : 'SETTING_REJECTED');
    return false;
  }
}

export function createCommandSession(
  catalog?: CatalogState,
  panels?: { history: boolean; memory_explorer: boolean },
  options: CommandSessionOptions = {},
) {
  const state = $state(initialCommandState());
  if (panels) state.panels = panels;
  const store = createCommandStore(state, { applyTheme: options.setTheme });
  let memorySaving = $state(false);
  let memoryError = $state('');
  let changeUndoing = $state(false);
  let changeError = $state('');
  let changeStatus = $state('');
  let memoryRevision = $state(0);

  function apply(result: CommandResult): boolean {
    if (!store.apply(result)) return false;
    if (result.change) {
      changeError = '';
      changeStatus = '';
    }
    if (catalog && result.settings) {
      catalog.revision = result.settings.revision;
      catalog.savedVoiceId = result.settings.approved_voice_id ?? '';
      if (result.action === 'SelectApprovedVoice') catalog.voiceId = catalog.savedVoiceId;
    }
    return true;
  }

  const setCaptions = (visible: boolean) => writeCaptions(visible, state.settings.revision, apply, store.fail);

  /**
   * Memory mode is server-owned and follows the same authoritative-snapshot
   * path as captions. A rejected write leaves the previous snapshot in place.
   * A revision conflict adopts the current server snapshot and retries once,
   * mirroring the voice picker's recovery for a commit made by another tab.
   */
  async function setMemoryMode(mode: MemoryMode): Promise<boolean> {
    if (memorySaving) return false;
    memorySaving = true;
    memoryError = '';
    const revision = state.settings.revision;
    const requestId = `memory:${revision}:${mode}`;
    try {
      const result = await saveMemoryModeWithRetry(mode, revision, async () => {
        // Another tab committed first: adopt its authoritative snapshot through the same
        // hydrate path the worker resync uses, then retry once.
        store.hydrate(await fetchSettings());
        return state.settings.revision;
      });
      return apply({
        request_id: requestId,
        kind: result.changed ? 'executed' : 'unchanged',
        action: 'SetMemoryMode',
        settings: result.settings,
        navigation: null,
        theme: null,
        changed: result.changed,
        spoken: result.changed ? `Memory mode set to ${mode}.` : `Memory mode is already ${mode}.`,
        detail: null,
      });
    } catch (error) {
      const detail = error instanceof Error ? error.message : 'SETTING_REJECTED';
      memoryError = memoryFailureText(detail);
      store.fail('SetMemoryMode', requestId, detail);
      return false;
    } finally {
      memorySaving = false;
    }
  }

  const setSpeakingSpeed = (speed: number) => writeSpeakingSpeed(speed, state.settings.revision, apply, store.fail);

  /** Close the current change toast and clear any inline undo failure. */
  function dismissChange(): void {
    store.dismissChange();
    changeError = '';
    changeStatus = '';
  }

  /** Undo an auto-saved memory change; a second call while one is in flight is ignored. */
  async function undoChange(id: string): Promise<boolean> {
    if (changeUndoing) return false;
    changeUndoing = true;
    changeError = '';
    changeStatus = '';
    try {
      await undoChangeRequest(id);
      store.dismissChange(id);
      memoryRevision += 1;
      options.onMemoryChange?.();
      changeStatus = 'Undone.';
      return true;
    } catch (error) {
      changeError = changeFailureText(error instanceof Error ? error.message : 'CHANGE_UNDO_FAILED');
      return false;
    } finally {
      changeUndoing = false;
    }
  }

  return {
    state,
    get change(): MemoryChange | null {
      return state.change;
    },
    get changeError() {
      return changeError;
    },
    get changeStatus() {
      return changeStatus;
    },
    get changeUndoing() {
      return changeUndoing;
    },
    get memoryRevision() {
      return memoryRevision;
    },
    get memory(): MemoryControl {
      return { mode: state.settings.memory_mode ?? 'auto', saving: memorySaving, error: memoryError };
    },
    get memorySaving() {
      return memorySaving;
    },
    get memoryError() {
      return memoryError;
    },
    apply,
    hydrate: store.hydrate,
    setCaptions,
    setMemoryMode,
    setSpeakingSpeed,
    dismissChange,
    undoChange,
    openPanel: store.openPanel,
  };
}

export type CommandSession = ReturnType<typeof createCommandSession>;
