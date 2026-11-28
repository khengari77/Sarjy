import { friendlyVoiceError } from '../audio/livekitAdapter';

export type Words = {
  new: string;
  title: string;
  welcomeBack: string;
  intro: string;
  conversationTitle: string;
  connected: string;
  greeting: string;
  reconnecting: string;
  offline: string;
  audioBlocked: string;
  cancelConnecting: string;
  placeholder: string;
  send: string;
  call: string;
  end: string;
  endShort: string;
  mute: string;
  unmute: string;
  interrupt: string;
  retryCleanup: string;
  talk: string;
  micOn: string;
  micOff: string;
  memoryMode: string;
  memoryModeOff: string;
  memoryModeAsk: string;
  memoryModeAuto: string;
  memoryModeNote: string;
  undo: string;
  dismiss: string;
  delete: string;
  deleteAction: string;
  deleteConfirm: string;
  deleteWarning: string;
  deleteActive: string;
  deleteFailed: string;
  cancel: string;
  deleting: string;
};

type BaseWords = Omit<Words, 'audioBlocked'>;

const EN: BaseWords = {
  new: 'New conversation',
  title: 'Meet Sarjy',
  welcomeBack: 'Welcome back',
  intro: 'Click the orb to start talking.',
  conversationTitle: 'Your conversation',
  connected: 'Connected',
  greeting: 'Playing the greeting.',
  reconnecting: 'Reconnecting…',
  offline: 'Offline',
  cancelConnecting: 'Cancel connecting',
  placeholder: 'Write a message…',
  send: 'Send',
  call: 'Start call',
  end: 'End call',
  endShort: 'End',
  mute: 'Mute',
  unmute: 'Unmute microphone',
  interrupt: 'Interrupt',
  retryCleanup: 'Retry ending previous call',
  talk: 'Talk to Sarjy',
  micOn: 'Microphone on',
  micOff: 'Microphone muted',
  memoryMode: 'Memory',
  memoryModeOff: 'Off',
  memoryModeAsk: 'Ask first',
  memoryModeAuto: 'Automatic',
  memoryModeNote: 'Controls whether new facts are saved from this conversation.',
  undo: 'Undo',
  dismiss: 'Dismiss',
  delete: 'Delete',
  deleteAction: 'Delete this chat',
  deleteConfirm: 'Delete this chat?',
  deleteWarning: 'It and its temporary memories are erased permanently. Permanent memories are kept.',
  deleteActive: 'End the call before deleting this chat',
  deleteFailed: 'Could not delete this chat. Try again.',
  cancel: 'Cancel',
  deleting: 'Deleting…',
};

const AR: BaseWords = {
  new: 'محادثة جديدة',
  title: 'تعرّف على سَرْجي',
  welcomeBack: 'أهلًا بعودتك',
  intro: 'اضغط على الأوربة لبدء التحدث.',
  conversationTitle: 'محادثتك',
  connected: 'متصل',
  greeting: 'جارٍ تشغيل الترحيب.',
  reconnecting: 'جارٍ إعادة الاتصال…',
  offline: 'غير متصل',
  cancelConnecting: 'إلغاء الاتصال',
  placeholder: 'اكتب رسالتك…',
  send: 'إرسال',
  call: 'بدء مكالمة',
  end: 'إنهاء',
  endShort: 'إنهاء',
  mute: 'كتم الصوت',
  unmute: 'ألغِ كتم الميكروفون',
  interrupt: 'قاطع',
  retryCleanup: 'إعادة إنهاء المكالمة السابقة',
  talk: 'تحدث إلى سَرْجي',
  micOn: 'الميكروفون يعمل',
  micOff: 'الميكروفون مكتوم',
  memoryMode: 'الذاكرة',
  memoryModeOff: 'إيقاف',
  memoryModeAsk: 'اسأل أولًا',
  memoryModeAuto: 'تلقائي',
  memoryModeNote: 'يتحكم في حفظ المعلومات الجديدة من هذه المحادثة.',
  undo: 'تراجع',
  dismiss: 'تجاهل',
  delete: 'حذف',
  deleteAction: 'حذف هذه المحادثة',
  deleteConfirm: 'حذف هذه المحادثة؟',
  deleteWarning: 'ستُحذف المحادثة وذكرياتها المؤقتة نهائيًا. تُحفظ الذكريات الدائمة.',
  deleteActive: 'أنهِ المكالمة قبل حذف هذه المحادثة',
  deleteFailed: 'تعذّر حذف هذه المحادثة. حاول مرة أخرى.',
  cancel: 'إلغاء',
  deleting: 'جارٍ الحذف…',
};

export function wordsFor(arabic: boolean): Words {
  return {
    ...(arabic ? AR : EN),
    audioBlocked: friendlyVoiceError('AUDIO_PLAYBACK_BLOCKED', arabic),
  };
}
