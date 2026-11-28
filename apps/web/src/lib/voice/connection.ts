import type { SessionConfig } from '../api';
import { ensureMicrophoneAccess } from '../audio/microphone';
import type { VoiceRuntime } from '../audio/voice-runtime';
import type { CommandResult } from '../commands/commandStore';
import { errorCode } from './errors';
import { createConnectionActions } from './connectionActions';
import { startSessionAttempt } from './sessionAttempt';
import { createSessionReleaser } from './sessionRelease';
import { cancelConnection } from './cancelConnection';
import { applyFailedRelease } from './releaseOutcome';
import { toggleVoiceMute } from './voiceControls';
import type { MeterController } from './meters';
import { createCallTimer } from './callTiming';
import { clearGreeting, createBrowserGreetingPlayer, type ActiveGreeting, type GreetingPlayer } from './greeting';

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
  /** Browser-played greeting while it plays; also drives the stage caption. */
  greeting: ActiveGreeting | null;
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
    greeting: null,
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
  /** Injectable for tests; production uses the browser audio element. */
  greetingPlayer?: GreetingPlayer;
  /** Maximum wait for the clip once the room is live; injectable for tests. */
  greetingMaxWaitMs?: number;
}

/** Read through a call so a session set while a release was awaited is visible. */
function liveSessionId(state: VoiceState): string | null {
  return state.live?.id ?? null;
}

function startCallTimer() {
  const timer = createCallTimer();
  timer.mark('press');
  return timer;
}

function clearOwnedLive(state: VoiceState, deps: ConnectionDeps, sessionId: string) {
  if (state.live?.id !== sessionId) return;
  deps.meters.teardownMeters();
  state.live = null;
  state.muted = true;
  state.activeVoiceName = '';
  state.connectionLost = false;
  state.provisional = null;
  state.greeting = null;
}

function resetCallState(state: VoiceState) {
  state.connecting = false;
  state.live = null;
  state.muted = true;
  state.connectionLost = false;
  state.provisional = null;
  state.greeting = null;
  state.transportStatus = 'idle';
  state.agentState = null;
  state.activeVoiceName = '';
  state.voiceErrorCode = '';
}

export function createVoiceConnection(state: VoiceState, deps: ConnectionDeps) {
  // Ownership epoch: bumped before admission so an attempt owns the connection state.
  let generation = 0;
  const endingSessions = new Set<string>();
  let pendingSession: SessionConfig | null = null;
  const releaseSession = createSessionReleaser();
  const greetingPlayer = deps.greetingPlayer ?? createBrowserGreetingPlayer();
  /** Skip (or tear down) the greeting: stop the clip and drop its caption. */
  const stopGreeting = () => clearGreeting(state, greetingPlayer);
  // A teardown that began at `epoch` is replaced by a newer attempt or a different session.
  const replacingSession = (epoch: number, sessionId: string) => {
    if (generation !== epoch) return true;
    const liveId = liveSessionId(state);
    return (liveId !== null && liveId !== sessionId) || (pendingSession !== null && pendingSession.id !== sessionId);
  };
  // A stale admission must not claim pending ownership.
  const ownPendingSession = (session: SessionConfig, attempt: number) => {
    if (attempt === generation) pendingSession = session;
  };
  async function connect(microphone: boolean) {
    if (state.live) {
      if (state.greeting) stopGreeting();
      if (microphone && state.muted) await toggleVoiceMute(state, deps);
      return;
    }
    if (state.connecting) return;
    // Called synchronously inside the click so iOS/Safari allow later playback.
    if (microphone) greetingPlayer.prepare();
    const attempt = ++generation;
    const timer = startCallTimer();
    pendingSession = null;
    state.connecting = true;
    state.voiceErrorCode = '';
    state.connectionLost = false;
    try {
      let conversationId = deps.getConversationId();
      if (!conversationId) {
        conversationId = await deps.ensureConversation();
        if (attempt !== generation) return;
      }
      timer.mark('conversation_ready');
      if (microphone) {
        try {
          await ensureMicrophoneAccess();
        } catch (error) {
          if (attempt === generation) state.voiceErrorCode = errorCode(error);
          return;
        }
        if (attempt !== generation) return;
        timer.mark('mic_ready');
      }
      await startSessionAttempt(state, deps, {
        microphone,
        conversationId,
        attempt,
        timer,
        isCurrent: () => attempt === generation && deps.getConversationId() === conversationId,
        releaseSession,
        clearOwnedLive: (sessionId) => clearOwnedLive(state, deps, sessionId),
        onSessionCreated: ownPendingSession,
        greetingPlayer,
        greetingMaxWaitMs: deps.greetingMaxWaitMs,
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
        timer.report();
      }
    }
  }

  async function cancelConnect() {
    const epoch = ++generation;
    stopGreeting();
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
    const session: SessionConfig | null = state.live ?? pendingSession;
    // Teardown ownership follows the session, not the module: ending call A
    // must never make End a no-op for a replacement call B.
    if (session && endingSessions.has(session.id)) return;
    if (session) endingSessions.add(session.id);
    const endEpoch = ++generation;
    pendingSession = null;
    stopGreeting();
    deps.meters.teardownMeters();
    resetCallState(state);
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

  return createConnectionActions({
    state,
    deps,
    connect,
    cancelConnect,
    end,
    releaseSession,
    stopGreeting,
    isEnding: (sessionId) => endingSessions.has(sessionId),
  });
}

export type VoiceConnection = ReturnType<typeof createVoiceConnection>;
