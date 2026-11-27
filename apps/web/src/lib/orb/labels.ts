/**
 * Voice/orb vocabulary and copy: phases, overlays, actions, their labels and
 * accessible text. Kept apart from the derivation so the state machine and the
 * strings stay inside the file-length budget.
 */

export type VoicePhase = 'idle' | 'connecting' | 'listening' | 'thinking' | 'speaking' | 'disconnected';

export type OrbOverlay = 'none' | 'memory_work' | 'confirming' | 'muted' | 'error';

export type OrbAction = 'start' | 'cancel' | 'mute' | 'unmute' | 'interrupt' | 'end' | 'retry' | 'reconnect';

export interface LocalizedText {
  en: string;
  ar: string;
}

export const PHASE_LABELS: Record<VoicePhase, LocalizedText> = {
  idle: { en: 'Ready', ar: 'جاهز' },
  connecting: { en: 'Connecting…', ar: 'جارٍ الاتصال…' },
  listening: { en: 'Listening…', ar: 'أستمع…' },
  thinking: { en: 'Thinking…', ar: 'أفكر…' },
  speaking: { en: 'Speaking…', ar: 'أتحدث…' },
  disconnected: { en: 'Offline', ar: 'غير متصل' },
};

/** Spoken/announced while the transport is reconnecting during a live call. */
export const RECONNECTING_LABEL: LocalizedText = { en: 'Reconnecting…', ar: 'جارٍ إعادة الاتصال…' };

export const OVERLAY_LABELS: Partial<Record<OrbOverlay, LocalizedText>> = {
  memory_work: { en: 'Checking memory…', ar: 'أراجع الذاكرة…' },
  confirming: { en: 'Confirmation needed', ar: 'مطلوب تأكيد' },
  muted: { en: 'Microphone muted', ar: 'الميكروفون مكتوم' },
  error: { en: 'Something went wrong', ar: 'حدث خطأ ما' },
};

export const OVERLAY_ARIA: Partial<Record<OrbOverlay, LocalizedText>> = {
  memory_work: { en: 'Checking memory.', ar: 'أراجع الذاكرة.' },
  confirming: { en: 'Confirmation needed before continuing.', ar: 'يلزم تأكيد قبل المتابعة.' },
  muted: { en: 'Microphone muted.', ar: 'الميكروفون مكتوم.' },
};

/** Phase description only; the offered action is appended from the resolved set. */
export const PHASE_ARIA: Record<VoicePhase | 'error', LocalizedText> = {
  idle: { en: '', ar: '' },
  connecting: { en: 'Connecting.', ar: 'جارٍ الاتصال.' },
  listening: { en: 'Listening.', ar: 'أستمع.' },
  thinking: { en: 'Thinking.', ar: 'أفكر.' },
  speaking: { en: 'Speaking.', ar: 'أتحدث.' },
  disconnected: { en: 'Offline.', ar: 'غير متصل.' },
  error: { en: 'Something went wrong.', ar: 'حدث خطأ ما.' },
};

export const ACTION_ARIA: Record<OrbAction, LocalizedText> = {
  start: { en: 'Start voice conversation', ar: 'ابدأ محادثة صوتية' },
  cancel: { en: 'Cancel connection', ar: 'إلغاء الاتصال' },
  mute: { en: 'Mute microphone', ar: 'اكتم الميكروفون' },
  unmute: { en: 'Unmute microphone', ar: 'ألغِ كتم الميكروفون' },
  interrupt: { en: 'Interrupt Sarjy', ar: 'قاطع سَرْجي' },
  end: { en: 'End conversation', ar: 'إنهاء المحادثة' },
  retry: { en: 'Retry connection', ar: 'أعد المحاولة' },
  reconnect: { en: 'Reconnect', ar: 'أعد الاتصال' },
};

export const ACTIONS_BY_PHASE: Record<VoicePhase | 'error', OrbAction[]> = {
  idle: ['start'],
  connecting: ['cancel'],
  listening: ['mute', 'end'],
  thinking: ['interrupt', 'end'],
  speaking: ['interrupt', 'end'],
  disconnected: ['reconnect'],
  error: ['retry'],
};

/**
 * The single action the accessible name advertises. It is always drawn from
 * `enabledActions`, so the name cannot offer something the state disallows
 * (the muted regression: "… Mute microphone" while `mute` was removed).
 */
export function advertisedAction(actions: readonly OrbAction[]): OrbAction | null {
  const priority: readonly OrbAction[] = ['unmute', 'mute', 'interrupt', 'cancel', 'retry', 'reconnect', 'start'];
  return priority.find((action) => actions.includes(action)) ?? null;
}

export function joinAria(parts: LocalizedText[]): LocalizedText {
  return {
    en: parts.map((part) => part.en).join(' '),
    ar: parts.map((part) => part.ar).join(' '),
  };
}
