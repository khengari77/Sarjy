import type { SessionConfig } from '../api';
import { endVoiceSession } from '../chat/session';
import { ensureMicrophoneAccess } from '../audio/microphone';
import type { VoiceEvent, VoiceRuntime } from '../audio/voice-runtime';
import { errorCode } from './errors';
import { startSessionAttempt } from './sessionAttempt';
import { interruptVoice, sendVoiceText, toggleVoiceMute } from './voiceControls';
import type { MeterController } from './meters';

export interface ProvisionalTranscript {
  id: string;
  text: string;
  speaker: 'user' | 'assistant';
}

export interface VoiceState {
  connecting: boolean;
  live: SessionConfig | null;
  muted: boolean;
  connectionLost: boolean;
  transportStatus: string;
  agentState: string | null;
  voiceErrorCode: string;
  activeVoiceName: string;
  provisional: ProvisionalTranscript | null;
  audioBlocked: boolean;
  micLevel: number;
  outputLevel: number;
}

export function initialVoiceState(): VoiceState {
  return {
    connecting: false,
    live: null,
    muted: true,
    connectionLost: false,
    transportStatus: 'idle',
    agentState: null,
    voiceErrorCode: '',
    activeVoiceName: '',
    provisional: null,
    audioBlocked: false,
    micLevel: 0,
    outputLevel: 0,
  };
}

export interface ConnectionDeps {
  ensureConversation(): Promise<string>;
  getConversationId(): string | null;
  refresh(): Promise<void>;
  getRuntime(): VoiceRuntime;
  getLanguage(): 'ar' | 'en';
  getVoice(): { id: string; name: string };
  meters: MeterController;
}

function handleVoiceEvent(state: VoiceState, deps: ConnectionDeps, event: VoiceEvent, isEnding: () => boolean) {
  if (event.type === 'status') {
    if (event.source === 'agent') {
      state.agentState = event.text;
    } else {
      state.agentState = null;
      state.transportStatus = event.text;
      if (event.text === 'disconnected' && state.live && !isEnding()) {
        deps.meters.teardownMeters();
        state.muted = true;
        state.connectionLost = true;
        state.provisional = null;
      }
    }
  }
  if (event.type === 'error') state.voiceErrorCode = event.code;
  if (event.type === 'audio-blocked') state.audioBlocked = true;
  if (event.type === 'transcript') {
    if (event.final) {
      state.provisional = null;
      void deps.refresh().catch(() => {});
    } else {
      state.provisional = { id: event.segmentId, text: event.text, speaker: event.speaker };
    }
  }
}

export function createVoiceConnection(state: VoiceState, deps: ConnectionDeps) {
  let attemptCounter = 0;
  let ending = false;
  const released = new Set<string>();

  async function releaseSession(session: SessionConfig | null) {
    if (!session || released.has(session.id)) return;
    released.add(session.id);
    await endVoiceSession(session).catch(() => {});
  }

  function ownsLive(sessionId: string) {
    return state.live?.id === sessionId;
  }

  function clearOwnedLive(sessionId: string) {
    if (!ownsLive(sessionId)) return;
    deps.meters.teardownMeters();
    state.live = null;
    state.muted = true;
    state.activeVoiceName = '';
    state.connectionLost = false;
    state.provisional = null;
  }

  async function connect(microphone: boolean) {
    if (state.live) {
      if (microphone && state.muted) await toggleVoiceMute(state, deps);
      return;
    }
    if (state.connecting) return;

    const attempt = ++attemptCounter;
    state.connecting = true;
    state.voiceErrorCode = '';
    state.connectionLost = false;

    try {
      let conversationId = deps.getConversationId();
      if (!conversationId) {
        conversationId = await deps.ensureConversation();
        if (attempt !== attemptCounter) return;
      }

      if (microphone) {
        try {
          await ensureMicrophoneAccess();
        } catch (error) {
          if (attempt === attemptCounter) state.voiceErrorCode = errorCode(error);
          return;
        }
        if (attempt !== attemptCounter) return;
      }

      await startSessionAttempt(state, deps, {
        microphone,
        conversationId,
        isCurrent: () => attempt === attemptCounter && deps.getConversationId() === conversationId,
        releaseSession,
        clearOwnedLive,
      });
    } catch (error) {
      if (attempt === attemptCounter) {
        state.voiceErrorCode = errorCode(error);
        state.transportStatus = 'disconnected';
      }
    } finally {
      if (attempt === attemptCounter) state.connecting = false;
    }
  }

  async function cancelConnect() {
    const wasConnecting = state.connecting;
    attemptCounter += 1;
    state.connecting = false;
    state.voiceErrorCode = '';
    state.transportStatus = 'idle';
    state.agentState = null;
    if (wasConnecting && state.live) {
      const session = state.live;
      clearOwnedLive(session.id);
      void releaseSession(session);
    }
    await deps
      .getRuntime()
      .cancelConnect()
      .catch(() => {});
  }

  async function end() {
    if (ending) return;
    ending = true;
    attemptCounter += 1;
    const session = state.live;
    deps.meters.teardownMeters();
    state.live = null;
    state.muted = true;
    state.connectionLost = false;
    state.provisional = null;
    state.transportStatus = 'idle';
    state.agentState = null;
    try {
      await deps
        .getRuntime()
        .disconnect()
        .catch(() => {});
      await releaseSession(session);
      await deps.refresh().catch(() => {});
    } finally {
      ending = false;
    }
  }

  async function reconnect() {
    await end();
    await connect(true);
  }

  function handleEvent(event: VoiceEvent) {
    handleVoiceEvent(state, deps, event, () => ending);
  }

  return {
    connect,
    reconnect,
    cancelConnect,
    end,
    toggleMute: () => toggleVoiceMute(state, deps),
    interrupt: () => interruptVoice(state, deps),
    sendText: (text: string) => sendVoiceText(state, deps, text),
    handleEvent,
  };
}

export type VoiceConnection = ReturnType<typeof createVoiceConnection>;
