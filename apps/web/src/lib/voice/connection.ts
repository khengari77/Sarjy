import type { SessionConfig } from '../api';
import { ensureMicrophoneAccess } from '../audio/microphone';
import type { VoiceEvent, VoiceRuntime } from '../audio/voice-runtime';
import { errorCode } from './errors';
import { startSessionAttempt } from './sessionAttempt';
import { createSessionReleaser } from './sessionRelease';
import { cancelConnection } from './cancelConnection';
import { applyFailedRelease } from './releaseOutcome';
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

function handleVoiceEvent(
  state: VoiceState,
  deps: ConnectionDeps,
  event: VoiceEvent,
  isEnding: (sessionId: string) => boolean,
) {
  if (event.type === 'status') {
    if (event.source === 'agent') {
      state.agentState = event.text;
    } else {
      state.agentState = null;
      state.transportStatus = event.text;
      if (event.text === 'disconnected' && state.live && !isEnding(state.live.id)) {
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

/** Read through a call so a session set while a release was awaited is visible. */
function liveSessionId(state: VoiceState): string | null {
  return state.live?.id ?? null;
}

function clearOwnedLive(state: VoiceState, deps: ConnectionDeps, sessionId: string) {
  if (state.live?.id !== sessionId) return;
  deps.meters.teardownMeters();
  state.live = null;
  state.muted = true;
  state.activeVoiceName = '';
  state.connectionLost = false;
  state.provisional = null;
}

export function createVoiceConnection(state: VoiceState, deps: ConnectionDeps) {
  let attemptCounter = 0;
  const endingSessions = new Set<string>();
  let pendingSession: SessionConfig | null = null;
  const releaseSession = createSessionReleaser();
  // Read through calls so a session created while a release was awaited is
  // visible to the guards below.
  const pendingSessionId = () => pendingSession?.id ?? null;
  const replacingSession = (sessionId: string) => {
    const liveId = liveSessionId(state);
    const pendingId = pendingSessionId();
    return (liveId !== null && liveId !== sessionId) || (pendingId !== null && pendingId !== sessionId);
  };
  async function connect(microphone: boolean) {
    if (state.live) {
      if (microphone && state.muted) await toggleVoiceMute(state, deps);
      return;
    }
    if (state.connecting) return;
    const attempt = ++attemptCounter;
    pendingSession = null;
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
        clearOwnedLive: (sessionId) => clearOwnedLive(state, deps, sessionId),
        onSessionCreated: (session) => {
          pendingSession = session;
        },
      });
      if (state.live) pendingSession = null;
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
    attemptCounter += 1;
    await cancelConnection({
      state,
      deps,
      takeSession: () => {
        const session = state.live ?? pendingSession;
        pendingSession = null;
        return session;
      },
      releaseSession,
      clearOwnedLive: (sessionId) => clearOwnedLive(state, deps, sessionId),
      isReplacing: replacingSession,
    });
  }

  async function end() {
    const session: SessionConfig | null = state.live ?? pendingSession;
    // Teardown ownership follows the session, not the module: ending call A
    // must never make End a no-op for a replacement call B.
    if (session && endingSessions.has(session.id)) return;
    if (session) endingSessions.add(session.id);
    attemptCounter += 1;
    pendingSession = null;
    deps.meters.teardownMeters();
    state.live = null;
    state.muted = true;
    state.connectionLost = false;
    state.provisional = null;
    state.transportStatus = 'idle';
    state.agentState = null;
    state.activeVoiceName = '';
    state.voiceErrorCode = '';
    try {
      await deps
        .getRuntime()
        .disconnect()
        .catch(() => {});
      const releasedOk = await releaseSession(session);
      if (!releasedOk && session) applyFailedRelease(state, session, replacingSession(session.id));
      await deps.refresh().catch(() => {});
    } finally {
      if (session) endingSessions.delete(session.id);
    }
  }

  async function reconnect() {
    await end();
    // A failed release leaves the old session live so End can retry it.
    // connect() would only unmute that dead session, so refuse instead.
    if (state.live) return;
    await connect(true);
  }

  function handleEvent(event: VoiceEvent) {
    handleVoiceEvent(state, deps, event, (sessionId) => endingSessions.has(sessionId));
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
