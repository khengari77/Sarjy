import type { SessionConfig } from '../api';
import type { VoiceEvent, VoiceRuntime } from '../audio/voice-runtime';
import type { CommandResult } from '../commands/commandStore';
import { errorCode } from './errors';
import { startSessionAttempt } from './sessionAttempt';
import { createSessionReleaser } from './sessionRelease';
import { cancelConnection } from './cancelConnection';
import { applyFailedRelease, retryFailedCleanup } from './releaseOutcome';
import { interruptVoice, sendVoiceText, toggleVoiceMute } from './voiceControls';
import type { MeterController } from './meters';
import { createConnectingPreview, startConnectingPreview } from './connectingPreview';

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
  /** Admission held by a session whose startup cleanup failed; retryable. */
  pendingCleanup: SessionConfig | null;
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
    pendingCleanup: null,
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
  /** Apply one backend-authored result to the shared command store. */
  applyCommand(result: CommandResult): void;
  /** Refetch the committed server settings after a lost command publication. */
  resyncSettings(): void;
  /** Fired when the server fell back from the saved voice; refresh server settings. */
  onVoiceFallback?(reason: 'removed' | 'transient'): void;
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
  if (event.type === 'command') deps.applyCommand(event.command);
  if (event.type === 'resync') deps.resyncSettings();
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

function beginAttempt(state: VoiceState) {
  state.connecting = true;
  // The orb must show the connecting phase from the tap, including while the
  // microphone permission prompt is open and before a session exists.
  state.transportStatus = 'connecting';
  state.voiceErrorCode = '';
  state.connectionLost = false;
}

function markMicrophoneFailure(state: VoiceState, attempt: number, generation: number, error: unknown) {  if (attempt !== generation) return;
  state.voiceErrorCode = errorCode(error);
  // A microphone refusal is not a transport attempt; return to idle so the
  // orb does not advertise a connection that never started.
  state.transportStatus = 'idle';
}

function sessionReplaced(
  state: VoiceState,
  epoch: number,
  generation: number,
  pendingSession: SessionConfig | null,
  sessionId: string,
): boolean {
  if (generation !== epoch) return true;
  const liveId = liveSessionId(state);
  return (liveId !== null && liveId !== sessionId) || (pendingSession !== null && pendingSession.id !== sessionId);
}

export function createVoiceConnection(state: VoiceState, deps: ConnectionDeps) {
  // Ownership epoch: bumped before admission starts so an attempt, not a
  // session id, owns the connection state.
  let generation = 0;
  const endingSessions = new Set<string>();
  let pendingSession: SessionConfig | null = null;
  const releaseSession = createSessionReleaser();
  // Connecting preview: one short-lived capture opened only after connect(true)
  // is called from an explicit user gesture. It is released on cancel, error,
  // unmount, or as soon as the live session publishes its own microphone.
  const preview = createConnectingPreview(state);

  // Read through calls so a session created while a release was awaited is
  // visible to the guards below.
  // A teardown that began at `epoch` has been replaced if a newer attempt
  // started since, id or no id yet; ids catch a session installed in place.
  const replacingSession = (epoch: number, sessionId: string) =>
    sessionReplaced(state, epoch, generation, pendingSession, sessionId);
  async function connect(microphone: boolean) {
    if (state.live) {
      if (microphone && state.muted) await toggleVoiceMute(state, deps);
      return;
    }
    if (state.connecting) return;
    const attempt = ++generation;
    pendingSession = null;
    beginAttempt(state);
    try {
      let conversationId = deps.getConversationId();
      if (!conversationId) {
        conversationId = await deps.ensureConversation();
        if (attempt !== generation) return;
      }
      if (microphone) {
        const ready = await startConnectingPreview(
          preview,
          () => attempt === generation,
          (error) => markMicrophoneFailure(state, attempt, generation, error),
        );
        if (!ready) return;
      }
      await startSessionAttempt(state, deps, {
        microphone,
        conversationId,
        attempt,
        isCurrent: () => attempt === generation && deps.getConversationId() === conversationId,
        releaseSession,
        clearOwnedLive: (sessionId) => clearOwnedLive(state, deps, sessionId),
        // The live session owns capture from here: release the preview before
        // LiveKit publishes its own microphone track.
        onSessionLive: preview.stop,
        onSessionCreated: (session, attempt) => {
          // A stale admission must not claim pending ownership.
          if (attempt === generation) pendingSession = session;
        },
      });
      if (state.live) pendingSession = null;
    } catch (error) {
      if (attempt === generation) {
        state.voiceErrorCode = errorCode(error);
        state.transportStatus = 'disconnected';
      }
    } finally {
      if (attempt === generation) {
        state.connecting = false;
        preview.stop();
      }
    }
  }

  async function cancelConnect() {
    preview.stop();
    const epoch = ++generation;
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
      isReplacing: (sessionId) => replacingSession(epoch, sessionId),
    });
  }

  async function end() {
    preview.stop();
    const session: SessionConfig | null = state.live ?? pendingSession;
    // Teardown ownership follows the session, not the module: ending call A
    // must never make End a no-op for a replacement call B.
    if (session && endingSessions.has(session.id)) return;
    if (session) endingSessions.add(session.id);
    const endEpoch = ++generation;
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
      if (!releasedOk && session) applyFailedRelease(state, session, replacingSession(endEpoch, session.id));
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

  return {
    connect,
    reconnect,
    cancelConnect,
    end,
    retryCleanup: () => retryFailedCleanup(state, releaseSession),
    toggleMute: () => toggleVoiceMute(state, deps),
    interrupt: () => interruptVoice(state, deps),
    sendText: (text: string) => sendVoiceText(state, deps, text),
    /** Release an in-flight connecting preview on page unmount. */
    disposePreview: preview.stop,
    handleEvent: (event: VoiceEvent) =>
      handleVoiceEvent(state, deps, event, (sessionId) => endingSessions.has(sessionId)),
  };
}

export type VoiceConnection = ReturnType<typeof createVoiceConnection>;
