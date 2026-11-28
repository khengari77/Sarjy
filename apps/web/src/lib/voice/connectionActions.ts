import type { SessionConfig } from '../api';
import type { VoiceEvent } from '../audio/voice-runtime';
import type { ConnectionDeps, VoiceState } from './connection';
import { createVoiceCommandEffects } from './commandEffects';
import { handleVoiceEvent } from './connectionEvents';
import { retryFailedCleanup } from './releaseOutcome';
import { interruptVoice, retryVoiceMicrophone, sendVoiceText, toggleVoiceMute } from './voiceControls';

export function createConnectionActions(context: {
  state: VoiceState;
  deps: ConnectionDeps;
  connect(microphone: boolean): Promise<void>;
  cancelConnect(): Promise<void>;
  end(): Promise<void>;
  releaseSession(session: SessionConfig | null): Promise<boolean>;
  stopGreeting(): void;
  isEnding(sessionId: string): boolean;
}) {
  const { state, deps } = context;
  const commandEffects = createVoiceCommandEffects({
    state,
    apply: deps.applyCommand,
    end: context.end,
    connect: context.connect,
    mute: () => toggleVoiceMute(state, deps),
    newConversation: async () => {
      await deps.newConversation?.();
    },
  });
  return {
    connect: context.connect,
    reconnect: async () => {
      commandEffects.cancelPending();
      await context.end();
      // A failed release leaves the old session live so End can retry it.
      // connect() would only unmute that dead session, so refuse instead.
      if (state.live) return;
      await context.connect(true);
    },
    cancelConnect: () => {
      commandEffects.cancelPending();
      return context.cancelConnect();
    },
    end: () => {
      // The user ended or cancelled first: a pending command end is obsolete.
      commandEffects.cancelPending();
      return context.end();
    },
    retryCleanup: () => retryFailedCleanup(state, context.releaseSession),
    retryMicrophone: () => {
      if (state.greeting) context.stopGreeting();
      return state.live ? retryVoiceMicrophone(state, deps) : context.connect(true);
    },
    toggleMute: () => {
      if (state.greeting) context.stopGreeting();
      return toggleVoiceMute(state, deps);
    },
    interrupt: () => {
      if (state.greeting) context.stopGreeting();
      return interruptVoice(state, deps);
    },
    sendText: (text: string) => {
      if (state.greeting) context.stopGreeting();
      return sendVoiceText(state, deps, text);
    },
    stopGreeting: context.stopGreeting,
    handleEvent: (event: VoiceEvent) => {
      if (event.type === 'command') {
        commandEffects.apply(event.command);
        return;
      }
      handleVoiceEvent(state, deps, event, (sessionId) => context.isEnding(sessionId), context.stopGreeting);
      // A transport "reconnecting/connected" status clears agentState without the
      // agent leaving its spoken reply; only agent and disconnect statuses count.
      if (event.type === 'status' && (event.source === 'agent' || event.text === 'disconnected')) {
        commandEffects.onAgentState();
      }
    },
  };
}
