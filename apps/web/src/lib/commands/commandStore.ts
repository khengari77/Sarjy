/**
 * One shared command/settings store for the GUI and finalized voice commands.
 *
 * The backend interprets a finalized transcript and publishes a CommandResult;
 * GUI controls apply the same typed-action result locally. Both funnel into
 * `apply`, which sets absolute state (never re-applies a delta) and ignores a
 * repeated request id. The UI never parses a transcript.
 */

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
  /** Client-applied effect; no server settings write is implied. */
  captions_visible: boolean | null;
  navigation: 'history' | 'memory_explorer' | null;
  changed: boolean;
  spoken: string;
  detail: string | null;
}

export interface PanelState {
  history: boolean;
  memory_explorer: boolean;
}

export interface CommandState {
  settings: SettingsSnapshot;
  panels: PanelState;
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
  if (
    value.captions_visible !== null &&
    value.captions_visible !== undefined &&
    typeof value.captions_visible !== 'boolean'
  ) {
    return null;
  }
  const navigation = value.navigation;
  if (navigation !== null && navigation !== undefined && navigation !== 'history' && navigation !== 'memory_explorer') {
    return null;
  }
  if (value.settings !== null && value.settings !== undefined && !isSettings(value.settings)) return null;
  return {
    request_id: value.request_id,
    kind: value.kind as CommandKind,
    action: value.action ?? null,
    settings: isSettings(value.settings) ? value.settings : null,
    captions_visible: typeof value.captions_visible === 'boolean' ? value.captions_visible : null,
    navigation: (navigation as CommandResult['navigation']) ?? null,
    changed: value.changed,
    spoken: value.spoken,
    detail: typeof value.detail === 'string' ? value.detail : null,
  };
}

export interface CommandStoreDeps {
  persist(settings: SettingsSnapshot): void;
}

export function createCommandStore(state: CommandState, deps: CommandStoreDeps) {
  let requestCounter = 0;

  function nextRequestId(): string {
    return `ui:${Date.now()}:${++requestCounter}`;
  }

  function apply(result: CommandResult): boolean {
    if (state.applied.includes(result.request_id)) return false;
    state.applied.push(result.request_id);
    if (result.settings) state.settings = result.settings;
    if (result.captions_visible !== null) {
      state.settings = { ...state.settings, captions_visible: result.captions_visible };
    }
    if (result.navigation === 'history') state.panels.history = true;
    if (result.navigation === 'memory_explorer') state.panels.memory_explorer = true;
    state.trace.push(result.action ?? result.kind);
    state.last = result;
    deps.persist(state.settings);
    return true;
  }

  function hydrate(settings: Partial<SettingsSnapshot>) {
    // A late bootstrap read must never clobber a newer applied command. A
    // local mirror has no revision and always applies.
    if (settings.revision !== undefined && settings.revision < state.settings.revision) return;
    state.settings = { ...state.settings, ...settings };
    deps.persist(state.settings);
  }

  /**
   * Captions are a client effect. The GUI and the worker result use the same
   * reducer, but neither claims a server settings write in this slice.
   */
  function setCaptions(visible: boolean): boolean {
    return apply({
      request_id: nextRequestId(),
      kind: 'executed',
      action: 'SetCaptionsVisible',
      settings: null,
      captions_visible: visible,
      navigation: null,
      changed: true,
      spoken: visible ? 'Captions shown.' : 'Captions hidden.',
      detail: null,
    });
  }

  /** GUI navigation writes the same panel store a voice result writes. */
  function openPanel(panel: keyof PanelState) {
    state.panels[panel] = true;
  }

  return { apply, hydrate, setCaptions, openPanel };
}

export type CommandStore = ReturnType<typeof createCommandStore>;
