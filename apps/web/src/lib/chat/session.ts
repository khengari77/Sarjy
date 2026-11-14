import {
  request,
  type Conversation,
  type SessionConfig,
  type SettingsActionResult,
  type StartVoice,
  type Turn,
  type UserSettings,
  type VoiceOption,
} from '../api';

export type ToolCall = { id: string; name: string; result: unknown };

export function mergeVoices(current: VoiceOption[], incoming: VoiceOption[]): VoiceOption[] {
  return [...new Map([...current, ...incoming].map((voice) => [voice.id, voice])).values()];
}

export async function createConversation(): Promise<Conversation> {
  return request<Conversation>('conversations', {});
}

export async function fetchConversation(id: string): Promise<{ turns: Turn[]; toolCalls: ToolCall[] }> {
  const [turns, toolCalls] = await Promise.all([
    request<Turn[]>('conversations/' + id + '/turns'),
    request<ToolCall[]>('conversations/' + id + '/tool-calls'),
  ]);
  return { turns, toolCalls };
}

export async function fetchSettings(): Promise<UserSettings> {
  return request<UserSettings>('settings');
}

export async function startVoiceSession(conversationId: string, language: 'ar' | 'en'): Promise<SessionConfig> {
  // Never send a voice id here: the server owns resolution (saved -> default) and
  // the saved voice's language. An explicit per-call override needs a distinct
  // user intent and must not be inferred from whatever the picker happens to show.
  const body = {
    conversation_id: conversationId,
    language,
    response_preference: 'automatic',
  } satisfies StartVoice;
  return request<SessionConfig>('voice/sessions', body);
}

/**
 * Persist the voice preference owner-side. No conversation is created: a fresh
 * browser must be able to save a preference before its first call.
 */
export async function saveVoiceSelection(voiceId: string, expectedRevision: number): Promise<UserSettings> {
  // Stable id for this exact intent: if the response is lost, retrying the same
  // selection at the same revision replays the committed outcome instead of 409.
  // An empty id is the explicit clear that restores the server default.
  const requestId = `voice:${expectedRevision}:${voiceId || 'default'}`;
  const result = await request<SettingsActionResult>('settings/actions', {
    request_id: requestId,
    expected_revision: expectedRevision,
    action: { kind: 'SelectApprovedVoice', voice_id: voiceId || null },
  });
  return result.settings;
}

export async function endVoiceSession(session: SessionConfig | null): Promise<void> {
  if (session) await request('voice/sessions/' + session.id + '/end', {});
}

export async function bootstrapConversations(): Promise<Conversation[]> {
  await request('demo-session', {});
  const conversations = await request<Conversation[]>('conversations');
  if (conversations.length) return conversations;
  return [await createConversation()];
}
