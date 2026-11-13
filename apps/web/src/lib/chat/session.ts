import { request, type Conversation, type SessionConfig, type StartVoice, type Turn, type VoiceOption } from '../api';

export type ToolCall = { id: string; name: string; result: unknown; turn_id?: string };

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

export async function startVoiceSession(
  conversationId: string,
  language: 'ar' | 'en',
  voiceId: string,
): Promise<SessionConfig> {
  const body = {
    conversation_id: conversationId,
    language,
    voice_id: voiceId || null,
    response_preference: 'automatic',
  } satisfies StartVoice;
  return request<SessionConfig>('voice/sessions', body);
}

export async function endVoiceSession(session: SessionConfig | null): Promise<void> {
  if (session) await request('voice/sessions/' + session.id + '/end', {});
}
