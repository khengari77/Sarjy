import { request } from '../api';
import type { VoiceRuntime } from '../audio/voice-runtime';
import { createVoiceRuntime } from '../audio/runtime-factory';
import type { CatalogState } from './catalog';
import { createCatalogStore } from './catalog';
import type { VoiceState } from './connection';
import { createVoiceConnection } from './connection';
import type { ConversationState } from './conversations';
import { createConversationStore } from './conversations';
import { bindVoicePage } from './pageLifecycle';
import { createMeterController } from './meters';

export type DevOverlay = 'none' | 'memory_work' | 'confirming' | 'muted' | 'error';

export interface StudioBoot {
  voice: VoiceState;
  catalog: CatalogState;
  conversation: ConversationState;
  restoreLanguage: boolean;
  setArabic(value: boolean): void;
  setCaptions(value: boolean): void;
  setDevOverlay(value: DevOverlay): void;
  run(work: () => Promise<void>, onError?: (error: unknown) => void): Promise<void>;
  getLanguage(): 'ar' | 'en';
  onVoiceError(error: unknown): void;
}

export function bootStudio(options: StudioBoot) {
  const { voice, catalog, conversation } = options;
  if (import.meta.env.DEV) {
    const params = new URLSearchParams(location.search);
    const fixture = params.get('phase');
    const overlay = params.get('overlay');
    if (fixture === 'idle' || fixture === 'disconnected' || fixture === 'connecting') voice.transportStatus = fixture;
    else if (fixture === 'listening' || fixture === 'thinking' || fixture === 'speaking') voice.agentState = fixture;
    if (overlay === 'memory_work' || overlay === 'confirming' || overlay === 'muted' || overlay === 'error')
      options.setDevOverlay(overlay);
  }
  if (options.restoreLanguage) options.setArabic(localStorage.getItem('sarjy-language') === 'ar');
  catalog.voiceId = localStorage.getItem('sarjy-voice-id') || '';
  options.setCaptions(localStorage.getItem('sarjy-captions') !== 'off');

  const runtime: VoiceRuntime = createVoiceRuntime();
  const meters = createMeterController(
    voice,
    () => runtime,
    () => voice.live !== null,
    () => voice.muted,
  );
  const catalogStore = createCatalogStore(catalog);
  const connection = createVoiceConnection(voice, {
    ensureConversation: () => conversations!.ensureConversation(),
    getConversationId: () => conversation.current,
    refresh: () => conversations!.refresh(),
    getRuntime: () => runtime,
    getLanguage: () => options.getLanguage(),
    getVoice: () => {
      const name = catalog.voices.find((entry) => entry.id === catalog.voiceId)?.name || catalog.voiceId;
      return { id: catalog.voiceId, name };
    },
    meters,
  });
  const conversations = createConversationStore(conversation, {
    getLive: () => voice.live !== null,
    endVoice: () => connection.end(),
  });
  const dispose = bindVoicePage({
    runtime,
    onEvent: (event) => connection.handleEvent(event),
    bootstrap: () =>
      options.run(async () => {
        await request('demo-session', {});
        await conversations.reload();
        if (conversation.conversations.length) await conversations.select(conversation.conversations[0].id);
      }),
    getLive: () => voice.live,
    endExpired: () => void options.run(() => connection.end(), options.onVoiceError),
    refresh: () => conversations.refresh(),
  });

  return {
    runtime,
    connection,
    conversations,
    catalogStore,
    cleanup: () => {
      meters.teardownMeters();
      dispose();
    },
  };
}

export type StudioBootResult = ReturnType<typeof bootStudio>;
