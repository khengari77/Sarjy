import type { components } from '../../../../packages/contracts/api';
export type VoiceOption = components['schemas']['VoiceOption'];
export type VoiceCatalog = components['schemas']['VoiceCatalog'];
export type PipelineConfig = components['schemas']['PipelineConfig'];
export type StartVoice = components['schemas']['StartVoice'];
export type UserSettings = components['schemas']['UserSettings'];
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
export interface SessionIntro {
  kind: 'full' | 'short';
  text: string;
  asset: string;
}
export interface SessionConfig {
  id: string;
  url: string;
  token: string;
  expires_at: string;
  voice: VoiceOption;
  voice_language: 'ar' | 'en';
  voice_source: 'requested' | 'saved' | 'default';
  saved_voice_unavailable?: 'removed' | 'transient' | null;
  /** Present when the browser opted into playing the greeting itself. */
  intro?: SessionIntro | null;
}
export interface SettingsActionResult {
  kind: 'SelectApprovedVoice' | 'AdjustSpeakingSpeed' | 'SetSpeakingSpeed' | 'SetCaptionsVisible' | 'SetMemoryMode';
  settings: UserSettings;
  changed: boolean;
}
export interface RequestOptions {
  /** Value returned for an empty successful body instead of API_RESPONSE_INVALID. */
  emptyBody?: unknown;
}

export async function request<T>(
  path: string,
  body?: unknown,
  method?: 'DELETE',
  options: RequestOptions = {},
): Promise<T> {
  const response = await fetch('/api/v1/' + path, {
    method: method ?? (body === undefined ? 'GET' : 'POST'),
    headers: body === undefined ? {} : { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  let data;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      throw new Error(response.ok ? 'API_RESPONSE_INVALID' : `HTTP_${response.status}`);
    }
  } else if (!response.ok) {
    throw new Error(`HTTP_${response.status}`);
  } else if (options.emptyBody !== undefined) {
    return options.emptyBody as T;
  } else {
    throw new Error('API_RESPONSE_INVALID');
  }
  if (!response.ok) {
    const code = data?.detail?.code;
    // The session ended elsewhere or expired: the page cannot work until the person signs in.
    if (code === 'ACCOUNT_SESSION_REQUIRED') globalThis.location?.assign('/sign-in');
    throw new Error(typeof code === 'string' && code ? code : `HTTP_${response.status}`);
  }
  return data;
}
