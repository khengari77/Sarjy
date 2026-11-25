export type Words = {
  new: string;
  title: string;
  welcomeBack: string;
  intro: string;
  conversationTitle: string;
  connected: string;
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
};

const EN: Words = {
  new: 'New conversation',
  title: 'Meet Sarjy',
  welcomeBack: 'Welcome back',
  intro: 'Click the orb to start talking.',
  conversationTitle: 'Your conversation',
  connected: 'Connected',
  placeholder: 'Write a message…',
  send: 'Send',
  call: 'Start call',
  end: 'End call',
  endShort: 'End',
  mute: 'Mute',
  unmute: 'Enable microphone',
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
};

const AR: Words = {
  new: 'محادثة جديدة',
  title: 'تعرّف على سَرْجي',
  welcomeBack: 'أهلًا بعودتك',
  intro: 'اضغط على الأوربة لبدء التحدث.',
  conversationTitle: 'محادثتك',
  connected: 'متصل',
  placeholder: 'اكتب رسالتك…',
  send: 'إرسال',
  call: 'بدء مكالمة',
  end: 'إنهاء',
  endShort: 'إنهاء',
  mute: 'كتم الصوت',
  unmute: 'تشغيل الميكروفون',
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
};

export function wordsFor(arabic: boolean): Words {
  return arabic ? AR : EN;
}
