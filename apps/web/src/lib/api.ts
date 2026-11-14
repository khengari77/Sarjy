import type { components } from '../../../../packages/contracts/api';
export type VoiceOption = components['schemas']['VoiceOption'];
export type VoiceCatalog = components['schemas']['VoiceCatalog'];
export type PipelineConfig = components['schemas']['PipelineConfig'];
export type StartVoice = components['schemas']['StartVoice'];

export interface UserSettings {
  speaking_speed: number;
  captions_visible: boolean;
  approved_voice_id: string | null;
  response_preference: string;
  revision: number;
}

export type VoiceControlStatus = 'idle' | 'pending' | 'dispatched' | 'applied' | 'failed' | 'superseded';

export interface VoiceControlState {
  session_id: string;
  conversation_id: string;
  active_voice_id: string | null;
  active_settings_revision: number;
  request_id: string | null;
  voice_id: string | null;
  settings_revision: number | null;
  status: VoiceControlStatus;
  error_code: string | null;
}

export interface VoiceActionResult {
  kind: string;
  settings: UserSettings;
  changed: boolean;
  voice_control?: VoiceControlState | null;
}

export interface ActionResponse {
  handled: boolean;
  result?: VoiceActionResult;
}
export interface Conversation {
  id: string;
  title: string;
  revision: number;
}
export interface Turn {
  id: string;
  role: string;
  text: string;
  status: string;
}
export interface SessionConfig {
  id: string;
  url: string;
  token: string;
  expires_at: string;
}
export async function request<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch('/api/v1/' + path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? {} : { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error(response.ok ? 'API_RESPONSE_INVALID' : `HTTP_${response.status}`);
  }
  if (!response.ok) {
    const code = data?.detail?.code;
    throw new Error(typeof code === 'string' && code ? code : `HTTP_${response.status}`);
  }
  return data;
}

export async function getSettings(): Promise<UserSettings> {
  return request<UserSettings>('settings');
}

export async function submitVoiceChange(input: {
  conversationId: string;
  requestId: string;
  expectedRevision: number;
  voiceId: string;
}): Promise<ActionResponse> {
  return request<ActionResponse>('actions', {
    conversation_id: input.conversationId,
    request_id: input.requestId,
    expected_revision: input.expectedRevision,
    source: 'ui',
    action: { kind: 'SelectApprovedVoice', voice_id: input.voiceId },
  });
}

export async function getVoiceControl(sessionId: string): Promise<VoiceControlState> {
  return request<VoiceControlState>('voice/sessions/' + encodeURIComponent(sessionId) + '/voice');
}
