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

export interface SettingsSnapshot {
  speaking_speed: number;
  captions_visible: boolean;
  approved_voice_id: string | null;
  response_preference: string;
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
}

export interface PanelState {
  history: boolean;
  memory_explorer: boolean;
}

export type RightTab = 'memories' | 'controls' | 'context' | 'transcript';

export interface CommandState {
  settings: SettingsSnapshot;
  panels: PanelState;
  /** Right-drawer tab selected by the last navigation command. */
  panelTab: RightTab;
  last: CommandResult | null;
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
    revision: 0,
  };
}

export function initialCommandState(): CommandState {
  return {
    settings: initialSettings(),
    panels: { history: false, memory_explorer: false },
    panelTab: 'controls',
    last: null,
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
    typeof value.revision === 'number' &&
    Number.isInteger(value.revision) &&
    value.revision >= 0
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
  if (value.settings !== null && value.settings !== undefined && !isSettings(value.settings)) return null;
  const theme = value.theme;
  if (theme !== null && theme !== undefined && !THEME_PREFERENCES.includes(theme as ThemePreference)) return null;
  return {
    request_id: value.request_id,
    kind: value.kind as CommandKind,
    action: value.action ?? null,
    settings: isSettings(value.settings) ? value.settings : null,
    navigation: (navigation as CommandResult['navigation']) ?? null,
    theme: THEME_PREFERENCES.includes(theme as ThemePreference) ? (theme as ThemePreference) : null,
    changed: value.changed,
    spoken: value.spoken,
    detail: typeof value.detail === 'string' ? value.detail : null,
  };
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
    // A stale settings snapshot must never rewind the authoritative state. The
    // worker may replay an old request id whose result predates another surface.
    if (result.settings && result.settings.revision < state.settings.revision) return false;
    state.applied.push(result.request_id);
    if (result.settings) state.settings = result.settings;
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
    state.trace.push(result.action ?? result.kind);
    state.last = result;
    return true;
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

  return { apply, hydrate, fail, openPanel };
}

export type CommandStore = ReturnType<typeof createCommandStore>;
