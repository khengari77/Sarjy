export type Words = {
  new: string;
  title: string;
  intro: string;
  placeholder: string;
  send: string;
  call: string;
  end: string;
  mute: string;
  unmute: string;
};

const EN: Words = {
  new: 'New conversation',
  title: 'Speak. Remember. Shape the context.',
  intro: 'Your voice assistant, with context you control.',
  placeholder: 'Write a message…',
  send: 'Send',
  call: 'Start call',
  end: 'End call',
  mute: 'Mute',
  unmute: 'Enable microphone',
};

const AR: Words = {
  new: 'محادثة جديدة',
  title: 'تحدث. تذكّر. تحكّم بالسياق.',
  intro: 'مساعدك الصوتي، وسياقه بين يديك.',
  placeholder: 'اكتب رسالتك…',
  send: 'إرسال',
  call: 'بدء مكالمة',
  end: 'إنهاء',
  mute: 'كتم الصوت',
  unmute: 'تشغيل الميكروفون',
};

export function wordsFor(arabic: boolean): Words {
  return arabic ? AR : EN;
}
