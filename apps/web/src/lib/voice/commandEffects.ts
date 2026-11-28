import type { CommandResult } from '../commands/commandStore';
import type { VoiceState } from './connection';

/** Safety cap while waiting for the agent to finish the spoken reply. */
export const END_AFTER_SPEECH_CAP_MS = 6_000;

/** Whole-utterance commands whose effect belongs to the browser, not the server. */
const CLIENT_EFFECT_ACTIONS: ReadonlySet<string> = new Set(['EndCall', 'MuteMicrophone', 'NewConversation']);

export interface VoiceCommandEffectDeps {
  state: VoiceState;
  /** Apply the result to the shared command store; false means it was already applied. */
  apply(result: CommandResult): boolean | void;
  end(): Promise<void> | void;
  connect(microphone: boolean): Promise<void> | void;
  mute(): Promise<void> | void;
  newConversation(): Promise<void> | void;
  /** Injectable for deterministic tests; production uses the browser timers. */
  setTimeout?(handler: () => void, ms: number): ReturnType<typeof setTimeout>;
  clearTimeout?(handle: ReturnType<typeof setTimeout>): void;
}

interface PendingEnd {
  timer: ReturnType<typeof setTimeout>;
  run: () => Promise<void> | void;
  lastAgentState: string | null;
}

export function createVoiceCommandEffects(deps: VoiceCommandEffectDeps) {
  const setTimer = deps.setTimeout ?? ((handler: () => void, ms: number) => setTimeout(handler, ms));
  const clearTimer = deps.clearTimeout ?? ((handle: ReturnType<typeof setTimeout>) => clearTimeout(handle));
  let pending: PendingEnd | null = null;

  function clearPending() {
    if (pending === null) return;
    clearTimer(pending.timer);
    pending = null;
  }

  function schedule(run: () => Promise<void> | void) {
    clearPending();
    const timer = setTimer(() => {
      // The cap owns the fire when no agent-state transition arrives.
      pending = null;
      void run();
    }, END_AFTER_SPEECH_CAP_MS);
    pending = { timer, run, lastAgentState: deps.state.agentState };
  }

  /** Run a pending end once the spoken reply is over, or immediately on a disconnect. */
  function onAgentState() {
    if (pending === null) return;
    if (!deps.state.live) {
      // The user ended the call first; the pending command must not run.
      clearPending();
      return;
    }
    const next = deps.state.agentState;
    const leftSpeaking = pending.lastAgentState === 'speaking' && next !== 'speaking';
    pending.lastAgentState = next;
    if (!leftSpeaking) return;
    const { timer, run } = pending;
    clearTimer(timer);
    pending = null;
    void run();
  }

  function apply(result: CommandResult) {
    if (!CLIENT_EFFECT_ACTIONS.has(result.action ?? '')) {
      deps.apply(result);
      return;
    }
    const live = deps.state.live !== null;
    let revised = result;
    if (result.action === 'EndCall' && !live) {
      revised = { ...result, spoken: 'There is no call to end.' };
    } else if (result.action === 'MuteMicrophone') {
      if (!live) revised = { ...result, spoken: 'There is no call to mute.' };
      else if (deps.state.muted) revised = { ...result, spoken: 'The microphone is already muted.' };
    }
    // The store owns idempotency: a replayed request id applies no second effect.
    if (deps.apply(revised) === false) return;

    if (result.action === 'EndCall') {
      if (live) schedule(() => deps.end());
      return;
    }
    if (result.action === 'MuteMicrophone') {
      // Never toggle: an unmuted live microphone is muted once here.
      if (live && !deps.state.muted) void deps.mute();
      return;
    }
    if (!live) {
      void deps.newConversation();
      return;
    }
    schedule(async () => {
      await deps.end();
      await deps.newConversation();
      // Only a call that was live reconnects; the new empty chat stays selected
      // and the normal error UI reports a failed reconnect.
      await deps.connect(true);
    });
  }

  return { apply, onAgentState, cancelPending: clearPending };
}
