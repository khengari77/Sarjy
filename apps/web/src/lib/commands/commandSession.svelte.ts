/** One reactive command session: the GUI and worker results share this state. */

import { fetchSettings, saveCaptionSelection, saveMemoryMode, saveSpeakingSpeed } from '../chat/session';
import type { CatalogState } from '../voice/catalog';
import type { ThemePreference } from '../ui/theme';
import { createCommandStore, initialCommandState, type CommandResult, type MemoryMode } from './commandStore';

export interface CommandSessionOptions {
  /**
   * Applies a browser-local theme through the shell's theme session. The
   * server never stores a theme, so this is the only effect for `SetTheme`.
   */
  setTheme?: (preference: ThemePreference) => void;
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

  function apply(result: CommandResult): boolean {
    if (!store.apply(result)) return false;
    if (catalog && result.settings) {
      catalog.revision = result.settings.revision;
      catalog.savedVoiceId = result.settings.approved_voice_id ?? '';
      if (result.action === 'SelectApprovedVoice') catalog.voiceId = catalog.savedVoiceId;
    }
    return true;
  }

  /**
   * Captions are server-owned: this sends the canonical settings action and
   * applies the authoritative snapshot. A rejected write changes nothing.
   */
  async function setCaptions(visible: boolean): Promise<boolean> {
    const revision = state.settings.revision;
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
      store.fail('SetCaptionsVisible', requestId, error instanceof Error ? error.message : 'SETTING_REJECTED');
      return false;
    }
  }

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
    async function writeMemoryMode(expectedRevision: number) {
      try {
        return await saveMemoryMode(mode, expectedRevision);
      } catch (error) {
        if (!(error instanceof Error && error.message === 'SETTINGS_REVISION_CONFLICT')) throw error;
        // Another tab committed first: adopt its authoritative snapshot through
        // the same hydrate path the worker resync uses, then retry once.
        store.hydrate(await fetchSettings());
        return saveMemoryMode(mode, state.settings.revision);
      }
    }
    try {
      const result = await writeMemoryMode(revision);
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
      memoryError =
        detail === 'SETTINGS_REVISION_CONFLICT'
          ? 'Settings changed in another window. Try again.'
          : "Couldn't change memory mode. Nothing was changed.";
      store.fail('SetMemoryMode', requestId, detail);
      return false;
    } finally {
      memorySaving = false;
    }
  }

  /**
   * Speed is a server-owned preference read by the worker at the next turn.
   * A rejected write leaves the previous snapshot in place.
   */
  async function setSpeakingSpeed(speed: number): Promise<boolean> {
    const revision = state.settings.revision;
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
      store.fail('SetSpeakingSpeed', requestId, error instanceof Error ? error.message : 'SETTING_REJECTED');
      return false;
    }
  }

  return {
    state,
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
    openPanel: store.openPanel,
  };
}

export type CommandSession = ReturnType<typeof createCommandSession>;
