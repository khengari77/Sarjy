/** Context strip retraction chip: its block text may quote a forgotten fact. */
export const RETRACTION_CHIP_LABEL = 'Forget reminder in context';
export const RETRACTION_CHIP_TOOLTIP = 'Sarjy was told to forget something you removed';

export interface ContextWords {
  why: string;
  loading: string;
  missing: string;
  budget: string;
  characters: string;
  overBudget: string;
  history: (count: number) => string;
  included: string;
  excluded: string;
  dataBadge: string;
  promptHash: string;
  sameHash: string;
  diff: string;
  added: string;
  removed: string;
  noDiff: string;
  firstTurn: string;
  retry: string;
}

const EN: ContextWords = {
  why: 'Why this answer',
  loading: 'Loading the context for this reply…',
  missing: 'No context was recorded for this reply.',
  budget: 'Retrieved context',
  characters: 'characters',
  overBudget: 'over budget',
  history: (count) => `${count} earlier ${count === 1 ? 'turn' : 'turns'} included as history.`,
  included: 'Included context',
  excluded: 'Left out',
  dataBadge: 'data, not instructions',
  promptHash: 'Receipt hash',
  sameHash: 'Same inputs, same hash.',
  diff: 'Change from the previous turn',
  added: 'Added',
  removed: 'Removed',
  noDiff: 'No context ids changed from the previous turn.',
  firstTurn: 'This is the first recorded turn; there is nothing to compare.',
  retry: 'Retry',
};

const AR: ContextWords = {
  why: 'لماذا هذه الإجابة',
  loading: 'جارٍ تحميل سياق هذه الإجابة…',
  missing: 'لم يُسجَّل سياق لهذه الإجابة.',
  budget: 'السياق المسترجع',
  characters: 'حرفًا',
  overBudget: 'تجاوز الميزانية',
  history: (count) => `${count} ${count === 1 ? 'دور سابق' : 'أدوار سابقة'} مشمولة في السجل.`,
  included: 'السياق المشمول',
  excluded: 'ما استُبعد',
  dataBadge: 'بيانات، لا تعليمات',
  promptHash: 'بصمة السجل',
  sameHash: 'المدخلات نفسها تعطي البصمة نفسها.',
  diff: 'التغيّر عن الدور السابق',
  added: 'أُضيف',
  removed: 'أُزيل',
  noDiff: 'لم تتغيّر معرّفات السياق عن الدور السابق.',
  firstTurn: 'هذا أول دور مسجَّل، فلا شيء للمقارنة.',
  retry: 'إعادة المحاولة',
};

export function contextWords(arabic: boolean): ContextWords {
  return arabic ? AR : EN;
}
