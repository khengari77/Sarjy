import type { SessionConfig } from '../api';
import { markIntroComplete, startVoiceSession } from '../chat/session';
import type { ConnectionDeps, VoiceState } from './connection';
import type { CallTimer } from './callTiming';
import { errorCode } from './errors';
import type { ActiveGreeting, GreetingPlayer } from './greeting';

/** Measured from the room becoming live, not from when the clip started. */
export const GREETING_MAX_WAIT_MS = 25_000;

async function waitForGreeting(
  promise: Promise<'ended' | 'failed'>,
  maxWaitMs: number,
): Promise<'ended' | 'failed' | 'timeout'> {
  if (!(maxWaitMs > 0)) return promise;
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      promise,
      new Promise<'timeout'>((resolve) => {
        timer = setTimeout(() => resolve('timeout'), maxWaitMs);
      }),
    ]);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}

function greetingWarner() {
  let warned = false;
  return () => {
    if (warned) return;
    warned = true;
    console.warn('[Sarjy] Browser greeting did not finish; starting the microphone.');
  };
}

const greetingEnded = (outcome: 'ended' | 'failed' | null) => outcome === 'ended';

function stopOwnedGreeting(state: VoiceState, greeting: ActiveGreeting | null, player: GreetingPlayer) {
  if (greeting === null || state.greeting?.attempt !== greeting.attempt) return;
  player.stop();
  state.greeting = null;
}

function startBrowserGreeting(
  state: VoiceState,
  intro: NonNullable<SessionConfig['intro']>,
  attempt: number,
  player: GreetingPlayer,
  timer: CallTimer | undefined,
  warn: () => void,
  onOutcome: (outcome: 'ended' | 'failed') => void,
) {
  const greeting: ActiveGreeting = { text: intro.text, kind: intro.kind, attempt };
  state.greeting = greeting;
  timer?.mark('greeting_started');
  // Runs while room.connect() and agent startup proceed; never awaited here.
  const promise = player.play('/greetings/' + intro.asset);
  void promise.then((outcome) => {
    onOutcome(outcome);
    // A deliberate stop clears the caption first; only a real failure warns.
    const owned = state.greeting?.attempt === greeting.attempt;
    if (owned) state.greeting = null;
    timer?.mark('greeting_ended');
    if (outcome === 'failed') {
      timer?.mark('greeting_failed');
      if (owned) warn();
    }
  });
  return { greeting, promise };
}

/** Wait for play(), but stop a stalled clip and treat it as failed. */
async function waitForGreetingOutcome(
  state: VoiceState,
  greeting: ActiveGreeting,
  promise: Promise<'ended' | 'failed'>,
  player: GreetingPlayer,
  timer: CallTimer | undefined,
  maxWaitMs: number,
  warn: () => void,
): Promise<'ended' | 'failed'> {
  const outcome = await waitForGreeting(promise, maxWaitMs);
  if (outcome !== 'timeout') return outcome;
  // A superseded attempt must not stop the newer attempt's shared player.
  if (state.greeting?.attempt !== greeting.attempt) return 'failed';
  player.stop();
  state.greeting = null;
  timer?.mark('greeting_ended');
  timer?.mark('greeting_failed');
  warn();
  return 'failed';
}

/**
 * One startup attempt. Once the room is live the microphone waits for the
 * browser greeting; while it plays, Cancel ends the call and the orb action
 * skips the greeting.
 */
export async function startSessionAttempt(
  state: VoiceState,
  deps: ConnectionDeps,
  context: {
    microphone: boolean;
    conversationId: string;
    attempt: number;
    timer?: CallTimer;
    isCurrent(): boolean;
    releaseSession(session: SessionConfig | null): Promise<boolean>;
    clearOwnedLive(sessionId: string): void;
    onSessionCreated?(session: SessionConfig, attempt: number): void;
    greetingPlayer: GreetingPlayer;
    greetingMaxWaitMs?: number;
  },
) {
  const voice = deps.getVoice();
  context.timer?.mark('session_request_start');
  // The text-only path keeps the worker greeting; only a microphone call has
  // the browser player standing by to mask the cold start.
  const config = await startVoiceSession(context.conversationId, deps.getLanguage(), context.microphone);
  context.timer?.mark('session_response');
  if (config.saved_voice_unavailable) deps.onVoiceFallback?.(config.saved_voice_unavailable);
  // Register the server session before awaiting the transport so cancel/end
  // can release it even while runtime.connect() is still pending.
  context.onSessionCreated?.(config, context.attempt);
  // Every startup exit collects the release result; a failed release is logged
  // with the session and the path that abandoned it instead of vanishing.
  const release = async (path: string) => {
    if (await context.releaseSession(config)) return true;
    console.warn('[Sarjy] Voice session release failed', { sessionId: config.id, path });
    // The server may still hold admission; keep it retryable from the UI
    // instead of dropping it when state.live is empty.
    state.pendingCleanup ??= config;
    return false;
  };

  if (!context.isCurrent()) {
    await release('superseded-before-connect');
    return;
  }

  let greeting: ActiveGreeting | null = null;
  let greetingOutcome: 'ended' | 'failed' | null = null;
  let greetingPromise: Promise<'ended' | 'failed'> | null = null;
  const warnGreetingFailure = greetingWarner();
  const stopMine = () => stopOwnedGreeting(state, greeting, context.greetingPlayer);

  if (context.microphone && config.intro) {
    const started = startBrowserGreeting(
      state,
      config.intro,
      context.attempt,
      context.greetingPlayer,
      context.timer,
      warnGreetingFailure,
      (outcome) => (greetingOutcome = outcome),
    );
    greeting = started.greeting;
    greetingPromise = started.promise;
  }

  try {
    state.transportStatus = 'connecting';
    state.agentState = null;
    const runtime = deps.getRuntime();
    await runtime.connect(config, context.timer);
    context.timer?.mark('room_connected');
    if (!context.isCurrent()) {
      stopMine();
      await release('superseded-during-connect');
      return;
    }
    state.live = config;
    state.activeVoiceName = voice.name;
    // Keep the microphone marked muted while the greeting plays, even live.
    state.muted = !context.microphone || greeting !== null;
    // A live room does not open the microphone while the greeting still plays.
    // A stalled clip must not stall the call: cap the wait from this moment.
    if (greetingPromise && greeting) {
      greetingOutcome = await waitForGreetingOutcome(
        state,
        greeting,
        greetingPromise,
        context.greetingPlayer,
        context.timer,
        context.greetingMaxWaitMs ?? GREETING_MAX_WAIT_MS,
        warnGreetingFailure,
      );
    }
    if (!context.isCurrent()) {
      stopMine();
      context.clearOwnedLive(config.id);
      await release('superseded-after-greeting');
      return;
    }
    // A drop during the clip leaves state.live set but the room gone: never
    // open the microphone or mark the intro against a dead call.
    const callLive = context.isCurrent() && !state.connectionLost && state.live?.id === config.id;
    if (context.microphone && callLive) {
      await runtime.startMicrophone();
      state.muted = false;
      context.timer?.mark('mic_started');
    }
    if (!context.isCurrent()) {
      stopMine();
      context.clearOwnedLive(config.id);
      await release('superseded-during-microphone');
      return;
    }
    // The intro is seen only once it ended and the call is live. If the clip
    // ended before the room did, this waits for the room above first.
    if (callLive && greeting?.kind === 'full' && greetingEnded(greetingOutcome)) {
      void markIntroComplete(config.id).catch(() => {
        console.warn('[Sarjy] Intro completion could not be recorded; it will replay next call.');
      });
    }
    deps.meters.setupMeters();
  } catch (error) {
    // Only a startup-boundary failure can be "did not connect"; a live-call
    // failure (such as the microphone) keeps its own code.
    const greetingPlayed =
      state.live?.id !== config.id && greeting !== null && greetingPromise !== null && greetingOutcome !== 'failed';
    stopMine();
    if (context.isCurrent()) {
      context.clearOwnedLive(config.id);
      state.muted = true;
      state.activeVoiceName = '';
      state.connectionLost = false;
      state.provisional = null;
      state.transportStatus = 'disconnected';
      state.agentState = null;
      // A greeting the caller actually heard deserves the distinct copy: the
      // intro is not marked, so a retry replays it.
      state.voiceErrorCode = greetingPlayed ? 'GREETING_UNCONNECTED' : errorCode(error);
      await deps
        .getRuntime()
        .disconnect()
        .catch(() => {});
    }
    await release('startup-failed');
  }
}
