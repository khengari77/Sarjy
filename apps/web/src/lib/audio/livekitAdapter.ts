import type { VoicePhase } from '../orb/orbState';

/**
 * Maps unstable LiveKit/agent strings onto the app's stable voice phases.
 * Keep this module pure so mapping rules can be unit-tested without a room.
 */

export function phaseFromTransportStatus(status: string): VoicePhase | null {
  switch (status) {
    case 'idle':
      // Initial/terminated application state; not an agent event.
      return 'idle';
    case 'connecting':
    case 'reconnecting':
    case 'starting':
      return 'connecting';
    case 'connected':
      return 'listening';
    case 'disconnected':
      return 'disconnected';
    default:
      return null;
  }
}

export function phaseFromAgentState(state: string | undefined | null): VoicePhase | null {
  switch (state) {
    case 'initializing':
    case 'connecting':
      return 'connecting';
    case 'listening':
      return 'listening';
    case 'thinking':
      return 'thinking';
    case 'speaking':
      return 'speaking';
    case 'idle':
      // Agent finished its turn and is ready for the user again.
      return 'listening';
    default:
      return null;
  }
}

/** Errors that make the current voice session unusable. */
const FATAL = new Set([
  'PIPELINE_NOT_CONFIGURED',
  'AGENT_START_TIMEOUT',
  'CONVERSATION_REQUIRED',
  'API_UNAVAILABLE',
  'CALL_LIMIT_REACHED',
  'DAILY_BUDGET_EXHAUSTED',
  'BUDGET_SERVICE_UNAVAILABLE',
  'VOICE_DEFAULT_UNCONFIGURED',
  'VOICE_DEFAULT_UNAVAILABLE',
  'VOICE_NOT_APPROVED',
  'VOICE_UNAVAILABLE',
  'VOICE_CATALOG_UNAVAILABLE',
  'VOICE_CATALOG_ACCESS_DENIED',
  'VOICE_PROVIDER_NOT_CONFIGURED',
]);

export function isFatalVoiceError(code: string): boolean {
  return FATAL.has(code);
}

const FRIENDLY: Record<string, { en: string; ar: string }> = {
  MICROPHONE_UNAVAILABLE: {
    en: 'Microphone unavailable. Allow microphone access in your browser settings, then try again.',
    ar: 'الميكروفون غير متاح. اسمح بالوصول إلى الميكروفون من إعدادات المتصفح ثم أعد المحاولة.',
  },
  MICROPHONE_PERMISSION_DENIED: {
    en: 'Microphone access is blocked. Allow it in your browser’s site settings, then retry. You can keep typing meanwhile.',
    ar: 'الوصول إلى الميكروفون محظور. اسمح به من إعدادات الموقع في المتصفح ثم أعد المحاولة. يمكنك متابعة الكتابة الآن.',
  },
  MICROPHONE_NOT_FOUND: {
    en: 'No microphone was found. Connect one or choose another input, then retry.',
    ar: 'لم يُعثر على ميكروفون. وصّل ميكروفونًا أو اختر إدخالًا آخر ثم أعد المحاولة.',
  },
  AUDIO_PLAYBACK_BLOCKED: {
    en: 'The browser blocked audio playback. Select “Enable audio” to continue.',
    ar: 'منع المتصفح تشغيل الصوت. اختر «تشغيل الصوت» للمتابعة.',
  },
  TRANSCRIPT_UNAVAILABLE: {
    en: 'The transcript could not be read. The voice session is still active.',
    ar: 'تعذّرت قراءة النص. جلسة الصوت ما زالت نشطة.',
  },
  CONVERSATION_REQUIRED: {
    en: 'Choose or create a conversation before starting voice.',
    ar: 'اختر محادثة أو أنشئ واحدة قبل بدء الصوت.',
  },
  PIPELINE_NOT_CONFIGURED: {
    en: 'Voice is not configured on this deployment.',
    ar: 'الصوت غير مُهيّأ في هذا النشر.',
  },
  AGENT_START_TIMEOUT: {
    en: 'The voice agent did not start in time. Try connecting again.',
    ar: 'لم يبدأ وكيل الصوت في الوقت المتوقع. حاول الاتصال مجددًا.',
  },
  API_UNAVAILABLE: {
    en: 'The service is temporarily unreachable. Try again shortly.',
    ar: 'الخدمة غير متاحة مؤقتًا. أعد المحاولة بعد قليل.',
  },
  CALL_LIMIT_REACHED: {
    en: 'You have reached the voice-chat limit for now. End or try later.',
    ar: 'بلغت حد محادثات الصوت حاليًا. أنهِ الجلسة أو أعد المحاولة لاحقًا.',
  },
  DAILY_BUDGET_EXHAUSTED: {
    en: 'The daily voice budget is exhausted. Voice will be available again later.',
    ar: 'نفدت ميزانية الصوت اليومية. سيتوفر الصوت مرة أخرى لاحقًا.',
  },
  BUDGET_SERVICE_UNAVAILABLE: {
    en: 'The usage guard is unavailable, so voice is paused to protect the budget.',
    ar: 'خدمة حد الاستخدام غير متاحة، لذا أُوقف الصوت حمايةً للميزانية.',
  },
  VOICE_DEFAULT_UNCONFIGURED: {
    en: 'No default voice is configured. Choose a voice and try again.',
    ar: 'لا يوجد صوت افتراضي مُعدّ. اختر صوتًا ثم أعد المحاولة.',
  },
  VOICE_DEFAULT_UNAVAILABLE: {
    en: 'The configured default voice is unavailable. Choose another voice.',
    ar: 'الصوت الافتراضي المُعدّ غير متاح. اختر صوتًا آخر.',
  },
  VOICE_NOT_APPROVED: {
    en: 'That voice is not approved. Choose a voice from the list.',
    ar: 'هذا الصوت غير معتمد. اختر صوتًا من القائمة.',
  },
  VOICE_UNAVAILABLE: {
    en: 'That voice is no longer available. Choose another voice.',
    ar: 'هذا الصوت لم يعد متاحًا. اختر صوتًا آخر.',
  },
  VOICE_CATALOG_UNAVAILABLE: {
    en: 'The voice catalog is unavailable. Try again later.',
    ar: 'قائمة الأصوات غير متاحة. أعد المحاولة لاحقًا.',
  },
  VOICE_CATALOG_ACCESS_DENIED: {
    en: 'The voice catalog cannot be accessed. Try again later.',
    ar: 'تعذّر الوصول إلى قائمة الأصوات. أعد المحاولة لاحقًا.',
  },
  VOICE_PROVIDER_NOT_CONFIGURED: {
    en: 'Voice service is not configured. Try again later.',
    ar: 'خدمة الصوت غير مُعدّة. أعد المحاولة لاحقًا.',
  },
  VOICE_SETTING_REJECTED: {
    en: 'The voice change could not be saved. Try again.',
    ar: 'تعذّر حفظ تغيير الصوت. أعد المحاولة.',
  },
  SESSION_RELEASE_FAILED: {
    en: 'The call ended, but the server could not confirm cleanup. Press End call again to retry.',
    ar: 'انتهت المكالمة، لكن الخادم لم يؤكد التنظيف. اضغط «إنهاء المكالمة» مرة أخرى لإعادة المحاولة.',
  },
};

/** Plain-language copy; never leaks keys, provider names or billing details. */
export function friendlyVoiceError(code: string, arabic: boolean): string {
  const entry = FRIENDLY[code];
  if (entry) return arabic ? entry.ar : entry.en;
  return arabic ? 'حدث خطأ ما. أعد المحاولة أو أنهِ الجلسة.' : 'Something went wrong. Try again or end the session.';
}
