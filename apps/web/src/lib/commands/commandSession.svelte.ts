/** One reactive command session: the GUI and worker results share this state. */

import { saveCaptionSelection, saveMemoryMode, saveSpeakingSpeed } from '../chat/session';
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
   */
  async function setMemoryMode(mode: MemoryMode): Promise<boolean> {
    const revision = state.settings.revision;
    const requestId = `memory:${revision}:${mode}`;
    try {
      const result = await saveMemoryMode(mode, revision);
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
      store.fail('SetMemoryMode', requestId, error instanceof Error ? error.message : 'SETTING_REJECTED');
      return false;
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
    apply,
    hydrate: store.hydrate,
    setCaptions,
    setMemoryMode,
    setSpeakingSpeed,
    openPanel: store.openPanel,
  };
}

export type CommandSession = ReturnType<typeof createCommandSession>;
