import type { components } from '../../../../packages/contracts/api';
export type VoiceOption = components['schemas']['VoiceOption'];
export type VoiceCatalog = components['schemas']['VoiceCatalog'];
export type PipelineConfig = components['schemas']['PipelineConfig'];
export type StartVoice = components['schemas']['StartVoice'];
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
