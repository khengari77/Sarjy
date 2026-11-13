/**
 * Voice/orb state model.
 *
 * Everything visual is derived from this module so that no event handler paints
 * colours directly (spec section 8.3). The derivation is pure: no fetch, no
 * storage, no side effects.
 */

import { mixHexColors } from './render/colors';
import {
  orbPalette,
  orbPhaseWeights,
  orbStateColors,
  type LivingPhase,
  type PaletteWeight,
  type ThemeName,
} from './palette';

export type { PaletteWeight, ThemeName };

export type VoicePhase = 'idle' | 'connecting' | 'listening' | 'thinking' | 'speaking' | 'disconnected';

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
  /** Theme used for the returned colours. Presentation-only input. */
  theme: ThemeName;
  /** Resolved phase after priority resolution (error/disconnected win). */
  effectivePhase: VoicePhase | 'error';
  primaryColor: string;
  accent: string | null;
  /** Palette weighting behind `primaryColor`, for the renderer. */
  paletteWeights: readonly PaletteWeight[];
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
  outputLevel: 0,
};

/**
 * The canonical phase/overlay vocabulary. Onboarding (#111) and Wordle are
 * deliberately absent: they are separate UI/game states and must never be
 * reported as a voice phase by this module.
 */
export const VOICE_PHASES: readonly VoicePhase[] = [
  'idle',
  'connecting',
  'listening',
  'thinking',
  'speaking',
  'disconnected',
];

export const ORB_OVERLAYS: readonly OrbOverlay[] = ['none', 'memory_work', 'confirming', 'muted', 'error'];

/** Phases that represent a live voice session (not idle/disconnected). */
export const LIVE_VOICE_PHASES: readonly VoicePhase[] = ['connecting', 'listening', 'thinking', 'speaking'];

/**
 * Priority rank of the state that wins presentation. Error outranks
 * disconnected, which outranks a live phase. Accents (confirming, memory_work,
 * muted) only decorate a live phase and never outrank it.
 */
export type OrbPriority = 'error' | 'disconnected' | 'phase' | 'confirming' | 'memory_work' | 'muted';

export function resolveOrbPriority(state: Pick<VoiceUiState, 'phase' | 'overlay'>): OrbPriority {
  if (state.overlay === 'error') return 'error';
  if (state.phase === 'disconnected') return 'disconnected';
  if (state.overlay === 'confirming') return 'confirming';
  if (state.overlay === 'memory_work') return 'memory_work';
  if (state.overlay === 'muted') return 'muted';
  return 'phase';
}

function motion(
  breath: number,
  flowRate: number,
  turbulence: number,
  edgeGlow: number,
  particleActivity: number,
  radialPulse: number,
  hueMix: number,
  opacity: number,
): MotionPreset {
  return { breath, flowRate, turbulence, edgeGlow, particleActivity, radialPulse, hueMix, opacity };
}

const MOTION: Record<VoicePhase | 'error', MotionPreset> = {
  idle: motion(0.22, 0.18, 0.18, 0.3, 0.18, 0, 0, 1),
  connecting: motion(0.3, 0.35, 0.24, 0.4, 0.3, 0, 0.35, 1),
  listening: motion(0.34, 0.42, 0.31, 0.55, 0.42, 0.8, 0, 1),
  thinking: motion(0.18, 0.56, 0.48, 0.48, 0.5, 0.15, 0, 1),
  speaking: motion(0.38, 0.52, 0.32, 0.6, 0.55, 0.85, 0, 1),
  disconnected: motion(0.04, 0.05, 0.05, 0.12, 0.04, 0, 0, 0.7),
  error: motion(0.1, 0.3, 0.5, 0.45, 0.3, 0, 0, 1),
};

const PHASE_LABELS: Record<VoicePhase, LocalizedText> = {
  idle: { en: 'Ready', ar: 'جاهز' },
  connecting: { en: 'Connecting…', ar: 'جارٍ الاتصال…' },
  listening: { en: 'Listening…', ar: 'أستمع…' },
  thinking: { en: 'Thinking…', ar: 'أفكر…' },
  speaking: { en: 'Speaking…', ar: 'أتحدث…' },
  disconnected: { en: 'Connection lost', ar: 'انقطع الاتصال' },
};

const OVERLAY_LABELS: Partial<Record<OrbOverlay, LocalizedText>> = {
  memory_work: { en: 'Checking memory…', ar: 'أراجع الذاكرة…' },
  confirming: { en: 'Confirmation needed', ar: 'مطلوب تأكيد' },
  muted: { en: 'Microphone muted', ar: 'الميكروفون مكتوم' },
  error: { en: 'Something went wrong', ar: 'حدث خطأ ما' },
};

const OVERLAY_ARIA: Partial<Record<OrbOverlay, LocalizedText>> = {
  memory_work: { en: 'Checking memory.', ar: 'أراجع الذاكرة.' },
  confirming: { en: 'Confirmation needed before continuing.', ar: 'يلزم تأكيد قبل المتابعة.' },
  muted: { en: 'Microphone muted, unmute microphone.', ar: 'الميكروفون مكتوم، ألغِ كتم الميكروفون.' },
  error: { en: 'Something went wrong, retry connection.', ar: 'حدث خطأ ما، أعد محاولة الاتصال.' },
};

const PHASE_ARIA: Record<VoicePhase | 'error', LocalizedText> = {
  idle: { en: 'Start voice conversation', ar: 'ابدأ محادثة صوتية' },
  connecting: { en: 'Connecting, cancel connection', ar: 'جارٍ الاتصال، إلغاء الاتصال' },
  listening: { en: 'Listening. Mute microphone', ar: 'أستمع. اكتم الميكروفون' },
  thinking: { en: 'Thinking. Interrupt Sarjy', ar: 'أفكر. قاطع سَرْجي' },
  speaking: { en: 'Speaking. Interrupt Sarjy', ar: 'يتحدث. قاطع سَرْجي' },
  disconnected: { en: 'Disconnected, reconnect', ar: 'انقطع الاتصال، أعد الاتصال' },
  error: { en: 'Error, retry connection', ar: 'خطأ، أعد المحاولة' },
};

const ACTIONS_BY_PHASE: Record<VoicePhase | 'error', OrbAction[]> = {
  idle: ['start'],
  connecting: ['cancel'],
  listening: ['mute', 'end'],
  thinking: ['interrupt', 'end'],
  speaking: ['interrupt', 'end'],
  disconnected: ['reconnect'],
  error: ['retry'],
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
  const priority = resolveOrbPriority(state);
  if (priority === 'error') return 'error';
  if (priority === 'disconnected') return 'disconnected';
  return state.phase;
}

/**
 * Blend the phase's palette weighting into one literal hex colour.
 * Exported so tests and the renderer share the exact same arithmetic.
 */
export function resolvePhaseColor(theme: ThemeName, phase: VoicePhase | 'error'): string {
  const stateColors = orbStateColors(theme);

  if (phase === 'error') return stateColors.error;

  const palette = orbPalette(theme);
  const weights = orbPhaseWeights(theme, phase as LivingPhase);
  if (weights.length === 0) return palette[0];

  let blended = palette[weights[0].index];
  let accumulated = weights[0].weight;
  for (const entry of weights.slice(1)) {
    accumulated += entry.weight;
    blended = mixHexColors(blended, palette[entry.index], entry.weight / accumulated);
  }

  // Disconnected keeps the palette slot but fades toward the theme slate
  // (docs 01: "gentle fade toward slate/blue gray").
  if (phase === 'disconnected') {
    return mixHexColors(blended, stateColors.disconnected, 0.55);
  }

  return blended;
}

function joinAria(parts: LocalizedText[]): LocalizedText {
  return {
    en: parts.map((part) => part.en).join(' '),
    ar: parts.map((part) => part.ar).join(' '),
  };
}

/**
 * Pure presentation derivation. Never performs I/O or mutates the input.
 *
 * `theme` is a presentation-only input: it may change colours, never phase,
 * overlay, actions, labels or session identity.
 */
export function deriveOrbPresentation(state: VoiceUiState, theme: ThemeName = 'light'): OrbPresentation {
  const effectivePhase = resolveEffectivePhase(state);
  const priority = resolveOrbPriority(state);
  const stateColors = orbStateColors(theme);
  const micLevel = clampLevel(state.micLevel);
  const outputLevel = clampLevel(state.outputLevel);

  const base = MOTION[effectivePhase];
  const pulse = effectivePhase === 'listening' ? micLevel : effectivePhase === 'speaking' ? outputLevel : 0;
  const motionPreset: MotionPreset = {
    ...base,
    radialPulse: Math.max(base.radialPulse * (0.35 + pulse * 0.65), pulse * base.radialPulse),
  };

  const overlayApplies = priority === 'confirming' || priority === 'memory_work' || priority === 'muted';

  let primaryColor = resolvePhaseColor(theme, effectivePhase);
  let accent: string | null = null;
  let accentOnly = false;
  let statusLabel = effectivePhase === 'error' ? OVERLAY_LABELS.error! : PHASE_LABELS[effectivePhase];
  let enabledActions = [...ACTIONS_BY_PHASE[effectivePhase]];
  let ariaParts: LocalizedText[] = [];
  let showMicSlash = !state.micActive && state.phase === 'listening' && effectivePhase === 'listening';

  if (overlayApplies) {
    accentOnly = true;
    ariaParts = [OVERLAY_ARIA[state.overlay]!];

    if (state.overlay === 'confirming') {
      accent = stateColors.confirmation;
      statusLabel = OVERLAY_LABELS.confirming!;
      if (!enabledActions.includes('end')) enabledActions.push('end');
    } else if (state.overlay === 'memory_work') {
      accent = stateColors.memory;
      statusLabel = OVERLAY_LABELS.memory_work!;
    } else {
      // Muted keeps the live phase colour but desaturates and shows a mic slash.
      // The agent's speaking/thinking label still wins: muting the microphone
      // must not hide that the assistant is producing a reply.
      primaryColor = mixHexColors(primaryColor, stateColors.muted, 0.5);
      accent = stateColors.muted;
      statusLabel =
        effectivePhase === 'speaking' || effectivePhase === 'thinking'
          ? PHASE_LABELS[effectivePhase]
          : OVERLAY_LABELS.muted!;
      enabledActions = enabledActions.filter((action) => action !== 'mute');
      if (!enabledActions.includes('unmute')) enabledActions.push('unmute');
      showMicSlash = true;
    }
  }

  if (effectivePhase === 'error') {
    accent = null;
    accentOnly = false;
    statusLabel = OVERLAY_LABELS.error!;
    ariaParts = [];
  }

  const ariaLabel =
    ariaParts.length > 0 ? joinAria([...ariaParts, PHASE_ARIA[effectivePhase]]) : PHASE_ARIA[effectivePhase];

  return {
    theme,
    effectivePhase,
    primaryColor,
    accent,
    paletteWeights: orbPhaseWeights(theme, (effectivePhase === 'error' ? state.phase : effectivePhase) as LivingPhase),
    motionPreset,
    statusLabel,
    ariaLabel,
    enabledActions,
    showMicSlash,
    accentOnly,
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

export * from './rendererTier';
