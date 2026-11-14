/** Per-browser display preferences. Nothing here touches the voice session. */

const CAPTIONS_KEY = 'sarjy-captions';
const LANGUAGE_KEY = 'sarjy-language';

export function storeCaptions(visible: boolean) {
  localStorage.setItem(CAPTIONS_KEY, visible ? 'on' : 'off');
}

export function storeLanguage(arabic: boolean) {
  localStorage.setItem(LANGUAGE_KEY, arabic ? 'ar' : 'en');
}
