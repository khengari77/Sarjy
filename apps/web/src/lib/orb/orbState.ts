/**
 * Voice/orb state model.
 *
 * Everything visual is derived from this module so that no event handler paints
 * colours directly (spec section 8.3). The derivation is pure: no fetch, no
 * storage, no side effects.
 */

export type VoicePhase =
  | 'idle'
  | 'connecting'
  | 'listening'
  | 'thinking'
  | 'speaking'
  | 'disconnected';

export type OrbOverlay = 'none' | 'memory_work' | 'confirming' | 'muted' | 'error';

export type OrbAction = 'start' | 'cancel' | 'mute' | 'unmute' | 'interrupt' | 'end' | 'retry' | 'reconnect';

export interface VoiceUiState {
  phase: VoicePhase;
  overlay: OrbOverlay;
  micActive: boolean;
  sessionId: string | null;
  connectionReady: boolean;
  /** Normalised 0-1, ephemeral. Local microphone envelope only. */
  micLevel: number;
  /** Normalised 0-1, ephemeral. Local playback envelope only. */
  outputLevel: number;
  errorMessage?: string;
  confirmationMessage?: string;
}

export interface LocalizedText {
  en: string;
  ar: string;
}

export interface MotionPreset {
  breath: number;
  flowRate: number;
  turbulence: number;
  edgeGlow: number;
  particleActivity: number;
  radialPulse: number;
  hueMix: number;
  opacity: number;
}

export interface OrbPresentation {
  /** Resolved phase after priority resolution (error/disconnected win). */
  effectivePhase: VoicePhase | 'error';
  primaryColor: string;
  accent: string | null;
  motionPreset: MotionPreset;
  statusLabel: LocalizedText;
  ariaLabel: LocalizedText;
  enabledActions: OrbAction[];
  showMicSlash: boolean;
  /** True when the overlay is an accent over a live voice phase. */
  accentOnly: boolean;
}

export const INITIAL_STATE: VoiceUiState = {
  phase: 'idle',
  overlay: 'none',
  micActive: false,
  sessionId: null,
  connectionReady: false,
  micLevel: 0,
  outputLevel: 0
};

const COLORS = {
  idle: '#4cc9b6',
  connecting: '#58bbd9',
  listening: '#4da8ff',
  thinking: '#a684f7',
  speaking: '#4add91',
  disconnected: '#5e7189',
  error: '#f25565',
  memory: '#e9b454',
  confirmation: '#ffa65a'
} as const;

const MOTION: Record<VoicePhase | 'error', MotionPreset> = {
  idle: { breath: 0.22, flowRate: 0.18, turbulence: 0.18, edgeGlow: 0.3, particleActivity: 0.18, radialPulse: 0, hueMix: 0, opacity: 1 },
  connecting: { breath: 0.3, flowRate: 0.35, turbulence: 0.24, edgeGlow: 0.4, particleActivity: 0.3, radialPulse: 0, hueMix: 0.35, opacity: 1 },
  listening: { breath: 0.34, flowRate: 0.42, turbulence: 0.31, edgeGlow: 0.55, particleActivity: 0.42, radialPulse: 0.8, hueMix: 0, opacity: 1 },
  thinking: { breath: 0.18, flowRate: 0.56, turbulence: 0.48, edgeGlow: 0.48, particleActivity: 0.5, radialPulse: 0.15, hueMix: 0, opacity: 1 },
  speaking: { breath: 0.38, flowRate: 0.52, turbulence: 0.32, edgeGlow: 0.6, particleActivity: 0.55, radialPulse: 0.85, hueMix: 0, opacity: 1 },
  disconnected: { breath: 0.04, flowRate: 0.05, turbulence: 0.05, edgeGlow: 0.12, particleActivity: 0.04, radialPulse: 0, hueMix: 0, opacity: 0.7 },
  error: { breath: 0.1, flowRate: 0.3, turbulence: 0.5, edgeGlow: 0.45, particleActivity: 0.3, radialPulse: 0, hueMix: 0, opacity: 1 }
};

const PHASE_LABELS: Record<VoicePhase, LocalizedText> = {
  idle: { en: 'Ready', ar: 'جاهز' },
  connecting: { en: 'Connecting…', ar: 'جارٍ الاتصال…' },
  listening: { en: 'Listening…', ar: 'أستمع…' },
  thinking: { en: 'Thinking…', ar: 'أفكر…' },
  speaking: { en: 'Speaking…', ar: 'أتحدث…' },
  disconnected: { en: 'Disconnected', ar: 'انقطع الاتصال' }
};

const OVERLAY_LABELS: Partial<Record<OrbOverlay, LocalizedText>> = {
  memory_work: { en: 'Checking memory…', ar: 'أراجع الذاكرة…' },
  confirming: { en: 'Confirmation needed', ar: 'مطلوب تأكيد' },
  muted: { en: 'Microphone muted', ar: 'الميكروفون مكتوم' },
  error: { en: 'Something went wrong', ar: 'حدث خطأ ما' }
};

const PHASE_ARIA: Record<VoicePhase | 'error', LocalizedText> = {
  idle: { en: 'Start voice conversation', ar: 'ابدأ محادثة صوتية' },
  connecting: { en: 'Connecting, cancel connection', ar: 'جارٍ الاتصال، إلغاء الاتصال' },
  listening: { en: 'Listening. Mute microphone', ar: 'أستمع. اكتم الميكروفون' },
  thinking: { en: 'Thinking. Interrupt Sarjy', ar: 'أفكر. قاطع سَرْجي' },
  speaking: { en: 'Speaking. Interrupt Sarjy', ar: 'يتحدث. قاطع سَرْجي' },
  disconnected: { en: 'Disconnected, reconnect', ar: 'انقطع الاتصال، أعد الاتصال' },
  error: { en: 'Error, retry connection', ar: 'خطأ، أعد المحاولة' }
};

const ACTIONS_BY_PHASE: Record<VoicePhase | 'error', OrbAction[]> = {
  idle: ['start'],
  connecting: ['cancel'],
  listening: ['mute', 'end'],
  thinking: ['interrupt', 'end'],
  speaking: ['interrupt', 'end'],
  disconnected: ['reconnect'],
  error: ['retry']
};

function clampLevel(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

/**
 * Resolve the effective base phase using the spec priority order:
 * error > disconnected > voice phase.
 */
export function resolveEffectivePhase(state: Pick<VoiceUiState, 'phase' | 'overlay'>): VoicePhase | 'error' {
  if (state.overlay === 'error') return 'error';
  if (state.phase === 'disconnected') return 'disconnected';
  return state.phase;
}

/**
 * Pure presentation derivation. Never performs I/O or mutates the input.
 */
export function deriveOrbPresentation(state: VoiceUiState): OrbPresentation {
  const effectivePhase = resolveEffectivePhase(state);
  const micLevel = clampLevel(state.micLevel);
  const outputLevel = clampLevel(state.outputLevel);

  const base = MOTION[effectivePhase];
  const pulse = effectivePhase === 'listening' ? micLevel : effectivePhase === 'speaking' ? outputLevel : 0;
  const motionPreset: MotionPreset = {
    ...base,
    radialPulse: Math.max(base.radialPulse * (0.35 + pulse * 0.65), pulse * base.radialPulse)
  };

  let primaryColor: string = COLORS[effectivePhase];
  let accent: string | null = null;
  let accentOnly = false;
  let statusLabel = PHASE_LABELS[effectivePhase as VoicePhase] ?? PHASE_LABELS.idle;
  let enabledActions = ACTIONS_BY_PHASE[effectivePhase];
  const showMicSlash = state.overlay === 'muted' || (!state.micActive && state.phase === 'listening');

  if (effectivePhase !== 'error' && effectivePhase !== 'disconnected') {
    if (state.overlay === 'confirming') {
      accent = COLORS.confirmation;
      accentOnly = true;
      statusLabel = OVERLAY_LABELS.confirming!;
      if (!enabledActions.includes('end')) enabledActions = [...enabledActions, 'end'];
    } else if (state.overlay === 'memory_work') {
      accent = COLORS.memory;
      accentOnly = true;
      statusLabel = OVERLAY_LABELS.memory_work!;
    } else if (state.overlay === 'muted') {
      // Muted keeps the live phase colour but desaturates and shows a mic slash.
      primaryColor = `color-mix(in srgb, ${COLORS[effectivePhase]} 55%, #5e7189)`;
      accent = COLORS.memory;
      accentOnly = true;
      statusLabel = OVERLAY_LABELS.muted!;
      enabledActions = enabledActions.filter((action) => action !== 'mute');
      if (!enabledActions.includes('unmute')) enabledActions = [...enabledActions, 'unmute'];
    }
  }

  if (effectivePhase === 'error') {
    accent = null;
    accentOnly = false;
    statusLabel = OVERLAY_LABELS.error!;
  }

  const ariaLabel = PHASE_ARIA[effectivePhase];

  return {
    effectivePhase,
    primaryColor,
    accent,
    motionPreset,
    statusLabel,
    ariaLabel,
    enabledActions,
    showMicSlash,
    accentOnly
  };
}

/* ------------------------------------------------------------------ */
/* Session epoch guard                                                 */
/* ------------------------------------------------------------------ */

export interface SessionTag {
  sessionId: string | null;
  epoch: number;
}

export function isCurrentTag(current: SessionTag, incoming: SessionTag): boolean {
  if (incoming.epoch !== current.epoch) return incoming.epoch > current.epoch;
  return incoming.sessionId === current.sessionId;
}

export function nextEpoch(current: number): number {
  return current + 1;
}

/* ------------------------------------------------------------------ */
/* Renderer tier selection (spec 4.7)                                  */
/* ------------------------------------------------------------------ */

export type RendererTier = 'enhanced' | 'light' | 'static';

export interface RendererEnvironment {
  reducedMotion: boolean;
  webglAvailable: boolean;
  /** Measured frames per second, when known. */
  fps?: number;
}

export const MIN_FPS_TARGET = 30;

export function selectRendererTier(environment: RendererEnvironment): RendererTier {
  if (environment.reducedMotion) return 'static';
  if (!environment.webglAvailable) return 'light';
  if (typeof environment.fps === 'number' && environment.fps > 0 && environment.fps < MIN_FPS_TARGET) return 'light';
  return 'enhanced';
}

/**
 * Downward-only renderer fallback. A recovered frame rate never promotes the
 * tier again, which avoids oscillating between canvas and WebGL mid-session.
 */
export function nextRendererTier(tier: RendererTier, fps: number): RendererTier {
  if (tier === 'static') return 'static';
  if (!Number.isFinite(fps) || fps <= 0) return tier;
  if (fps < 12) return 'static';
  if (fps < MIN_FPS_TARGET) return 'light';
  return tier;
}
