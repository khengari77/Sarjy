/**
 * One shared command/settings store for the GUI and finalized voice commands.
 *
 * Backend-authored results and GUI typed-action results both funnel into
 * `apply`, which sets absolute state and ignores a repeated request id. The UI
 * never parses a transcript, and the server settings snapshot is authoritative
 * for every preference in this slice.
 */

import { THEME_PREFERENCES, type ThemePreference } from '../ui/theme';

export type CommandKind = 'executed' | 'unchanged' | 'needs_confirmation' | 'unsupported' | 'failed' | 'unknown';
export type MemoryMode = 'off' | 'ask' | 'auto';
export type MemoryChangeKind = 'memory_saved' | 'memory_updated';

/** A server-authored memory change that the person can still undo. */
export interface MemoryChange {
  id: string;
  kind: MemoryChangeKind;
  label: string;
  undoable: boolean;
  memory_id: string | null;
}

/** What the Memory capture control needs: the mode, whether a save is in flight, and the last failure. */
export interface MemoryControl {
  mode: MemoryMode;
  saving: boolean;
  error: string;
}

export interface SettingsSnapshot {
  speaking_speed: number;
  captions_visible: boolean;
  approved_voice_id: string | null;
  response_preference: string;
  /** Optional so payloads from before the memory-mode setting still validate. */
  memory_mode?: MemoryMode;
  revision: number;
}

export interface CommandResult {
  request_id: string;
  kind: CommandKind;
  action: string | null;
  settings: SettingsSnapshot | null;
  navigation: 'history' | 'memory_explorer' | null;
  /** Browser-local theme hint; never a server setting. */
  theme: ThemePreference | null;
  changed: boolean;
  spoken: string;
  detail: string | null;
  /** Present only for a MemoryChange result; it never carries a settings snapshot. */
  change?: MemoryChange | null;
}

export interface PanelState {
  history: boolean;
  memory_explorer: boolean;
}

export type RightTab = 'memories' | 'controls' | 'transcript';

export interface CommandState {
  settings: SettingsSnapshot;
  panels: PanelState;
  /** Right-drawer tab selected by the last navigation command. */
  panelTab: RightTab;
  last: CommandResult | null;
  /** Latest memory change that may still be undone; replaced by the next change. */
  change: MemoryChange | null;
  /** Applied typed-action identifiers, in order; observable for parity tests. */
  trace: string[];
  applied: string[];
}

const KINDS: ReadonlySet<string> = new Set([
  'executed',
  'unchanged',
  'needs_confirmation',
  'unsupported',
  'failed',
  'unknown',
]);

export function initialSettings(): SettingsSnapshot {
  return {
    speaking_speed: 1,
    captions_visible: true,
    approved_voice_id: null,
    response_preference: 'automatic',
    memory_mode: 'auto',
    revision: 0,
  };
}

export function initialCommandState(): CommandState {
  return {
    settings: initialSettings(),
    panels: { history: false, memory_explorer: false },
    panelTab: 'controls',
    last: null,
    change: null,
    trace: [],
    applied: [],
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isSettings(value: unknown): value is SettingsSnapshot {
  return (
    isRecord(value) &&
    typeof value.speaking_speed === 'number' &&
    Number.isFinite(value.speaking_speed) &&
    typeof value.captions_visible === 'boolean' &&
    (value.approved_voice_id === null || typeof value.approved_voice_id === 'string') &&
    typeof value.response_preference === 'string' &&
    (value.memory_mode === undefined ||
      value.memory_mode === 'off' ||
      value.memory_mode === 'ask' ||
      value.memory_mode === 'auto') &&
    typeof value.revision === 'number' &&
    Number.isInteger(value.revision) &&
    value.revision >= 0
  );
}

/** Actions that must carry the authoritative settings snapshot they changed. */
const SETTINGS_ACTIONS: ReadonlySet<string> = new Set([
  'SelectApprovedVoice',
  'AdjustSpeakingSpeed',
  'SetSpeakingSpeed',
  'SetCaptionsVisible',
  'SetMemoryMode',
]);

function isMemoryChange(value: unknown): value is MemoryChange {
  return (
    isRecord(value) &&
    typeof value.id === 'string' &&
    value.id.length > 0 &&
    (value.kind === 'memory_saved' || value.kind === 'memory_updated') &&
    typeof value.label === 'string' &&
    value.label.length > 0 &&
    typeof value.undoable === 'boolean' &&
    (value.memory_id === null || typeof value.memory_id === 'string')
  );
}

/** Validate a worker-published result before it can touch UI state. */
export function normalizeCommandResult(value: unknown): CommandResult | null {
  if (!isRecord(value)) return null;
  if (typeof value.request_id !== 'string' || !value.request_id) return null;
  if (typeof value.kind !== 'string' || !KINDS.has(value.kind)) return null;
  if (typeof value.spoken !== 'string' || !value.spoken) return null;
  if (typeof value.changed !== 'boolean') return null;
  if (value.action !== null && typeof value.action !== 'string') return null;
  if (value.detail !== null && value.detail !== undefined && typeof value.detail !== 'string') return null;
  const navigation = value.navigation;
  if (navigation !== null && navigation !== undefined && navigation !== 'history' && navigation !== 'memory_explorer') {
    return null;
  }
  const action = value.action ?? null;
  const memoryChange = action === 'MemoryChange';
  if (memoryChange) {
    // The auto-save result is identified by its change payload and is not a
    // settings write, even when a worker accidentally includes a snapshot.
    if (!isMemoryChange(value.change)) return null;
  } else {
    // Only an applied settings result must carry the authoritative snapshot.
    // Failed and unknown results deliberately report no settings at all.
    const applied = value.kind === 'executed' || value.kind === 'unchanged';
    if (action !== null && SETTINGS_ACTIONS.has(action) && applied && !isSettings(value.settings)) return null;
    if (value.settings !== null && value.settings !== undefined && !isSettings(value.settings)) return null;
    // A change object belongs only to MemoryChange; another action must not adopt it.
    if (value.change !== null && value.change !== undefined) return null;
  }
  const theme = value.theme;
  if (theme !== null && theme !== undefined && !THEME_PREFERENCES.includes(theme as ThemePreference)) return null;
  const change = isMemoryChange(value.change) ? value.change : null;
  const result: CommandResult = {
    request_id: value.request_id,
    kind: value.kind as CommandKind,
    action,
    settings: memoryChange ? null : isSettings(value.settings) ? value.settings : null,
    navigation: (navigation as CommandResult['navigation']) ?? null,
    theme: THEME_PREFERENCES.includes(theme as ThemePreference) ? (theme as ThemePreference) : null,
    changed: value.changed,
    spoken: value.spoken,
    detail: typeof value.detail === 'string' ? value.detail : null,
  };
  // Omit the key when there is no change so existing payload equality holds.
  if (change) result.change = change;
  return result;
}

export interface CommandStoreOptions {
  /**
   * Applies a browser-local theme once. The shell's theme session owns storage
   * and OS resolution; this store only decides *when* it applies.
   */
  applyTheme?: (preference: ThemePreference) => void;
}

export function createCommandStore(state: CommandState, options: CommandStoreOptions = {}) {
  function apply(result: CommandResult): boolean {
    if (state.applied.includes(result.request_id)) return false;
    const memoryChange = result.action === 'MemoryChange';
    // A stale settings snapshot must never rewind the authoritative state. The
    // worker may replay an old request id whose result predates another surface.
    if (!memoryChange && result.settings && result.settings.revision < state.settings.revision) return false;
    state.applied.push(result.request_id);
    // A memory change is never a settings write, even if a payload carries one.
    if (!memoryChange && result.settings) state.settings = result.settings;
    // Theme is presentation-only and only an executed/unchanged hint may touch
    // the shell. A failed, unsupported or unknown result changes nothing.
    if (result.theme && (result.kind === 'executed' || result.kind === 'unchanged')) {
      options.applyTheme?.(result.theme);
    }
    if (result.navigation === 'history') state.panels.history = true;
    if (result.navigation === 'memory_explorer') {
      state.panels.memory_explorer = true;
      state.panelTab = 'memories';
    }
    // A new change replaces the old toast; the settings path is untouched.
    if (result.change) state.change = result.change;
    state.trace.push(result.action ?? result.kind);
    state.last = result;
    return true;
  }

  /** Dismiss the current change, or only the one with this id if it is still current. */
  function dismissChange(id?: string) {
    if (id === undefined || state.change?.id === id) state.change = null;
  }

  function hydrate(settings: Partial<SettingsSnapshot>) {
    // A late bootstrap read must never clobber a newer applied command.
    if (settings.revision !== undefined && settings.revision < state.settings.revision) return;
    state.settings = { ...state.settings, ...settings };
  }

  /** Record a failed GUI gesture without changing state or the trace. */
  function fail(action: string, requestId: string, detail: string) {
    state.last = {
      request_id: requestId,
      kind: 'failed',
      action,
      settings: null,
      navigation: null,
      theme: null,
      changed: false,
      spoken: "I couldn't apply that. Nothing was changed.",
      detail,
    };
  }

  /** GUI navigation writes the same panel store a voice result writes. */
  function openPanel(panel: keyof PanelState) {
    state.panels[panel] = true;
  }

  return { apply, hydrate, fail, openPanel, dismissChange };
}

export type CommandStore = ReturnType<typeof createCommandStore>;
