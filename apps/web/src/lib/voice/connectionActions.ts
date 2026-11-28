import type { SessionConfig } from '../api';
import type { VoiceEvent } from '../audio/voice-runtime';
import type { ConnectionDeps, VoiceState } from './connection';
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
  return {
    connect: context.connect,
    reconnect: async () => {
      await context.end();
      // A failed release leaves the old session live so End can retry it.
      // connect() would only unmute that dead session, so refuse instead.
      if (state.live) return;
      await context.connect(true);
    },
    cancelConnect: context.cancelConnect,
    end: context.end,
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
    handleEvent: (event: VoiceEvent) =>
      handleVoiceEvent(state, deps, event, (sessionId) => context.isEnding(sessionId), context.stopGreeting),
  };
}
