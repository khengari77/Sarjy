import type { VoiceEvent } from '../audio/voice-runtime';
import type { ConnectionDeps, VoiceState } from './connection';
import { isAgentClearableError, setVoiceError } from './errors';

export function handleVoiceEvent(
  state: VoiceState,
  deps: ConnectionDeps,
  event: VoiceEvent,
  isEnding: (sessionId: string) => boolean,
  stopGreeting: () => void,
) {
  if (event.type === 'status') {
    if (event.source === 'agent') {
      state.agentState = event.text;
      // A healthy agent event only clears errors caused by the agent/transport.
      // Microphone errors clear on a successful unmute/retry, not a turn
      // boundary: the microphone is still off.
      const healthy =
        event.text === 'idle' || event.text === 'listening' || event.text === 'thinking' || event.text === 'speaking';
      if (healthy && isAgentClearableError(state.voiceErrorCode)) state.voiceErrorCode = '';
    } else {
      state.agentState = null;
      state.transportStatus = event.text;
      if (event.text === 'disconnected' && state.live && !isEnding(state.live.id)) {
        stopGreeting();
        deps.meters.teardownMeters();
        state.muted = true;
        state.connectionLost = true;
        state.provisional = null;
      }
    }
  }
  if (event.type === 'error') setVoiceError(state, event.code);
  if (event.type === 'audio-blocked') state.audioBlocked = true;
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
