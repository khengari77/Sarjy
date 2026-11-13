import {
  request,
  type ActionResponse,
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

export async function startVoiceSession(
  conversationId: string,
  language: 'ar' | 'en',
  voiceId?: string,
): Promise<SessionConfig> {
  // Omit the voice when the user has no saved selection: the server then applies
  // its approved default, so a fresh browser can start without opening the picker.
  const body = {
    conversation_id: conversationId,
    language,
    ...(voiceId ? { voice_id: voiceId } : {}),
    response_preference: 'automatic',
  } satisfies StartVoice;
  return request<SessionConfig>('voice/sessions', body);
}

export async function saveVoiceSelection(
  conversationId: string,
  voiceId: string,
  expectedRevision: number,
): Promise<UserSettings> {
  const response = await request<ActionResponse>('actions', {
    conversation_id: conversationId,
    request_id: 'voice-' + crypto.randomUUID(),
    expected_revision: expectedRevision,
    source: 'ui',
    action: { kind: 'SelectApprovedVoice', voice_id: voiceId },
  });
  if (!response.handled || !('settings' in response.result)) throw new Error('VOICE_SETTING_REJECTED');
  return (response.result as SettingsActionResult).settings;
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
