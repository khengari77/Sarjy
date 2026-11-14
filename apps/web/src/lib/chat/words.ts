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
};

const EN: Words = {
  new: 'New conversation',
  title: 'Meet Sarjy',
  welcomeBack: 'Welcome back',
  intro: 'Tap the cloud to start talking. Or type below.',
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
};

const AR: Words = {
  new: 'محادثة جديدة',
  title: 'تعرّف على سَرْجي',
  welcomeBack: 'أهلًا بعودتك',
  intro: 'اضغط على السحابة لبدء التحدث. أو اكتب أدناه.',
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
};

export function wordsFor(arabic: boolean): Words {
  return arabic ? AR : EN;
}
