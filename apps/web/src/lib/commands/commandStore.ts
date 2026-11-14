/**
 * One shared command/settings store for the GUI and finalized voice commands.
 *
 * The backend interprets a finalized transcript and publishes a CommandResult;
 * the GUI control sends the same typed action through `/v1/actions`. Both
 * funnel into `apply`, which sets absolute state (never applies a delta twice)
 * and ignores a repeated request id. The UI never parses a transcript.
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

export type ActionPayload =
  | { kind: 'AdjustSpeakingSpeed'; delta: number }
  | { kind: 'SetSpeakingSpeed'; speed: number }
  | { kind: 'SetCaptionsVisible'; visible: boolean };

const KINDS: ReadonlySet<string> = new Set([
  'executed',
  'unchanged',
  'needs_confirmation',
  'unsupported',
  'failed',
  'unknown',
]);

const SPEED_MIN = 0.8;
const SPEED_MAX = 1.2;

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
    navigation: (navigation as CommandResult['navigation']) ?? null,
    changed: value.changed,
    spoken: value.spoken,
    detail: typeof value.detail === 'string' ? value.detail : null,
  };
}

/**
 * Map the existing typed-action REST result to the same contract the worker
 * publishes. The GUI half of parity therefore uses one shape with voice.
 */
export function resultFromUiResponse(requestId: string, body: unknown): CommandResult | null {
  if (!isRecord(body) || body.handled !== true || !isRecord(body.result)) return null;
  const raw = body.result;
  if (isSettings(raw.settings) && typeof raw.changed === 'boolean' && typeof raw.kind === 'string') {
    return {
      request_id: requestId,
      kind: raw.changed ? 'executed' : 'unchanged',
      action: raw.kind,
      settings: raw.settings,
      navigation: null,
      changed: raw.changed,
      spoken: raw.changed ? 'Done.' : 'No change.',
      detail: null,
    };
  }
  const navigation = raw.navigation;
  if (navigation === 'history' || navigation === 'memory_explorer') {
    return {
      request_id: requestId,
      kind: 'executed',
      action: navigation === 'history' ? 'ShowConversationHistory' : 'OpenMemoryExplorer',
      settings: null,
      navigation,
      changed: true,
      spoken: navigation === 'history' ? 'Opening your previous conversations.' : 'Opening the memory explorer.',
      detail: null,
    };
  }
  return null;
}

export interface CommandTransport {
  /** Send one typed action; throws on a transport or protocol failure. */
  send(action: ActionPayload, requestId: string, expectedRevision: number): Promise<CommandResult | null>;
}

export interface CommandStoreDeps {
  persist(settings: SettingsSnapshot): void;
}

export function createCommandStore(state: CommandState, transport: CommandTransport, deps: CommandStoreDeps) {
  let queue: Promise<void> = Promise.resolve();
  let requestCounter = 0;

  function apply(result: CommandResult): boolean {
    if (state.applied.includes(result.request_id)) return false;
    state.applied.push(result.request_id);
    if (result.settings) state.settings = result.settings;
    if (result.navigation === 'history') state.panels.history = true;
    if (result.navigation === 'memory_explorer') state.panels.memory_explorer = true;
    state.trace.push(result.action ?? result.kind);
    state.last = result;
    deps.persist(state.settings);
    return true;
  }

  function failed(action: ActionPayload, requestId: string, detail: string): CommandResult {
    const result: CommandResult = {
      request_id: requestId,
      kind: 'failed',
      action: action.kind,
      settings: null,
      navigation: null,
      changed: false,
      spoken: "I couldn't apply that. Nothing was changed.",
      detail,
    };
    state.last = result;
    return result;
  }

  function run(action: ActionPayload): Promise<CommandResult | null> {
    const task = queue.then(async () => {
      const requestId = `ui:${Date.now()}:${++requestCounter}`;
      try {
        const result = await transport.send(action, requestId, state.settings.revision);
        if (result) apply(result);
        return result;
      } catch (error) {
        failed(action, requestId, error instanceof Error ? error.message : 'ACTION_FAILED');
        return null;
      }
    });
    queue = task.then(
      () => undefined,
      () => undefined,
    );
    return task;
  }

  function hydrate(settings: Partial<SettingsSnapshot>) {
    state.settings = { ...state.settings, ...settings };
    deps.persist(state.settings);
  }

  function setCaptions(visible: boolean): Promise<CommandResult | null> {
    return run({ kind: 'SetCaptionsVisible', visible });
  }

  function setSpeed(speed: number): Promise<CommandResult | null> {
    const clamped = Math.min(SPEED_MAX, Math.max(SPEED_MIN, Math.round(speed * 10) / 10));
    const difference = Math.round((clamped - state.settings.speaking_speed) * 10) / 10;
    if (difference === 0) return Promise.resolve(null);
    if (Math.abs(difference) === 0.1) {
      return run({ kind: 'AdjustSpeakingSpeed', delta: difference });
    }
    return run({ kind: 'SetSpeakingSpeed', speed: clamped });
  }

  /** GUI navigation writes the same panel store a voice result writes. */
  function openPanel(panel: keyof PanelState) {
    state.panels[panel] = true;
  }

  return { apply, run, hydrate, setCaptions, setSpeed, openPanel };
}

export type CommandStore = ReturnType<typeof createCommandStore>;
