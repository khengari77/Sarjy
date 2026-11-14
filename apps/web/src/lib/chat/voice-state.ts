import type { VoiceOption } from '../api';

/**
 * UI status for voice selection. "Active" is reserved for a confirmed worker
 * voice; this slice never fabricates that confirmation (#112 owns the ACK path).
 */
export type VoiceStatus = 'default-next-call' | 'selected-next-call' | 'selected-for-call' | 'confirmed-active';

export interface VoiceStatusInput {
  /** User/server selection that will apply to the next call. */
  selected: VoiceOption | null;
  /** Voice sent to the worker for the running call, not yet acknowledged. */
  session: VoiceOption | null;
  /** Voice the worker has acknowledged as active; null until #112 lands. */
  confirmed: VoiceOption | null;
  live: boolean;
}

export function selectedVoiceOption(voices: VoiceOption[], voiceId: string): VoiceOption | null {
  return (
    voices.find((entry) => entry.id === voiceId) ?? (voiceId ? { id: voiceId, name: voiceId, languages: [] } : null)
  );
}

export function voiceStatus({ selected, session, confirmed, live }: VoiceStatusInput): VoiceStatus {
  if (live && confirmed) return 'confirmed-active';
  // No worker ACK exists yet (#112), so a live call gets a static statement of
  // which voice it is using — never a confirmation that can never arrive.
  if (live && session) return 'selected-for-call';
  if (selected) return 'selected-next-call';
  return 'default-next-call';
}

export function voiceStatusText(input: VoiceStatusInput, arabic: boolean): string {
  const status = voiceStatus(input);
  const voice = status === 'selected-for-call' ? input.session : input.selected;
  const name = voice?.name || voice?.id || '';
  switch (status) {
    case 'confirmed-active':
      return arabic ? `الصوت النشط: ${name}` : `Active voice: ${name}`;
    case 'selected-for-call':
      return arabic ? `صوت هذه المكالمة: ${name}` : `Voice for this call: ${name}`;
    case 'selected-next-call':
      return arabic ? `محدد للمكالمة التالية: ${name}` : `Selected for next call: ${name}`;
    case 'default-next-call':
      return arabic
        ? 'سيُستخدم الصوت الافتراضي المعتمد من الخادم للمكالمة التالية.'
        : 'The server-approved default voice will be used for the next call.';
  }
}
