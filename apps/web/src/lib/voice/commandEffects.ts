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
  /** True/undefined when the chat was created; false when creation failed. */
  newConversation(): Promise<boolean | void> | boolean | void;
  /** Injectable for deterministic tests; production uses the browser timers. */
  setTimeout?(handler: () => void, ms: number): ReturnType<typeof setTimeout>;
  clearTimeout?(handle: ReturnType<typeof setTimeout>): void;
}

interface PendingEnd {
  timer: ReturnType<typeof setTimeout>;
  run: (token: number) => Promise<void> | void;
  lastAgentState: string | null;
  token: number;
}

export function createVoiceCommandEffects(deps: VoiceCommandEffectDeps) {
  const setTimer = deps.setTimeout ?? ((handler: () => void, ms: number) => setTimeout(handler, ms));
  const clearTimer = deps.clearTimeout ?? ((handle: ReturnType<typeof setTimeout>) => clearTimeout(handle));
  let pending: PendingEnd | null = null;
  // Invalidates both a pending timer and an already-running async effect.
  let generation = 0;

  function invalidate() {
    generation += 1;
    if (pending === null) return;
    clearTimer(pending.timer);
    pending = null;
  }

  function schedule(run: (token: number) => Promise<void> | void) {
    invalidate();
    const token = generation;
    const timer = setTimer(() => {
      if (token !== generation) return;
      // The cap owns the fire when no agent-state transition arrives.
      pending = null;
      void run(token);
    }, END_AFTER_SPEECH_CAP_MS);
    pending = { timer, run, lastAgentState: deps.state.agentState, token };
  }

  /** Run a pending end once the spoken reply is over, or immediately on a disconnect. */
  function onAgentState() {
    if (pending === null) return;
    if (!deps.state.live) {
      // The user ended the call first; the pending command must not run.
      invalidate();
      return;
    }
    const next = deps.state.agentState;
    const leftSpeaking = pending.lastAgentState === 'speaking' && next !== 'speaking';
    pending.lastAgentState = next;
    if (!leftSpeaking) return;
    const { timer, run, token } = pending;
    clearTimer(timer);
    pending = null;
    void run(token);
  }

  function apply(result: CommandResult) {
    const action = result.action ?? '';
    // A failed, unsupported or unknown dispatch changes nothing: only an
    // executed result may reach a live call or the conversation store effect.
    if (!CLIENT_EFFECT_ACTIONS.has(action) || result.kind !== 'executed') {
      deps.apply(result);
      return;
    }
    const live = deps.state.live !== null;
    let revised = result;
    if (action === 'EndCall' && !live) {
      revised = { ...result, spoken: 'There is no call to end.' };
    } else if (action === 'MuteMicrophone' && !live) {
      revised = { ...result, spoken: 'There is no call to mute.' };
    }
    // The store owns idempotency: a replayed request id applies no second effect.
    if (deps.apply(revised) === false) return;

    if (action === 'EndCall') {
      if (live) schedule(() => deps.end());
      return;
    }
    if (action === 'MuteMicrophone') {
      // Never toggle: an unmuted live microphone is muted once here.
      if (live && !deps.state.muted) void deps.mute();
      return;
    }
    if (!live) {
      void deps.newConversation();
      return;
    }
    schedule(async (token) => {
      await deps.end();
      // A failed release restores the old session; never create a chat against it.
      if (token !== generation || deps.state.live !== null) return;
      const created = await deps.newConversation();
      if (created === false || token !== generation) return;
      // Only a call that was live reconnects; the new empty chat stays selected
      // and the normal error UI reports a failed reconnect.
      await deps.connect(true);
    });
  }

  return { apply, onAgentState, cancelPending: invalidate, dispose: invalidate };
}
