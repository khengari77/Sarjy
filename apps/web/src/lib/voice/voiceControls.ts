import type { ConnectionDeps, VoiceState } from './connection';
import {
  isAgentRecoverableError,
  isFatalVoiceError,
  isMicrophoneErrorCode,
  microphoneErrorCode,
  setVoiceError,
} from './errors';

/** One in-flight microphone action per connection; ignore concurrent clicks. */
const microphoneActions = new WeakSet<VoiceState>();

function ownsLive(state: VoiceState, sessionId: string): boolean {
  return state.live?.id === sessionId;
}

function clearMicrophoneError(state: VoiceState) {
  if (!isFatalVoiceError(state.voiceErrorCode) && isMicrophoneErrorCode(state.voiceErrorCode)) {
    state.voiceErrorCode = '';
  }
}

export async function toggleVoiceMute(state: VoiceState, deps: ConnectionDeps) {
  if (!state.live || microphoneActions.has(state)) return;
  const sessionId = state.live.id;
  const wasMuted = state.muted;
  microphoneActions.add(state);
  try {
    if (wasMuted) {
      await deps.getRuntime().startMicrophone();
      if (!ownsLive(state, sessionId) || state.muted !== wasMuted) return;
      state.muted = false;
      deps.meters.startMicMeter();
    } else {
      await deps.getRuntime().stopMicrophone();
      if (!ownsLive(state, sessionId) || state.muted !== wasMuted) return;
      state.muted = true;
      deps.meters.stopMicMeter();
    }
    clearMicrophoneError(state);
  } catch (error) {
    if (ownsLive(state, sessionId)) setVoiceError(state, microphoneErrorCode(error));
  } finally {
    microphoneActions.delete(state);
  }
}

export async function interruptVoice(state: VoiceState, deps: ConnectionDeps) {
  if (!state.live) return;
  const sessionId = state.live.id;
  await deps.getRuntime().interrupt();
  if (!ownsLive(state, sessionId)) return;
  state.agentState = 'listening';
  if (isAgentRecoverableError(state.voiceErrorCode)) state.voiceErrorCode = '';
}

/** Retry the microphone without tearing down a live call. */
export async function retryVoiceMicrophone(state: VoiceState, deps: ConnectionDeps) {
  if (!state.live || !state.muted || microphoneActions.has(state)) return;
  const sessionId = state.live.id;
  microphoneActions.add(state);
  try {
    await deps.getRuntime().startMicrophone();
    if (!ownsLive(state, sessionId) || !state.muted) return;
    state.muted = false;
    deps.meters.startMicMeter();
    clearMicrophoneError(state);
  } catch (error) {
    if (ownsLive(state, sessionId)) setVoiceError(state, microphoneErrorCode(error));
  } finally {
    microphoneActions.delete(state);
  }
}

export async function sendVoiceText(state: VoiceState, deps: ConnectionDeps, text: string) {
  if (!state.live) return;
  await deps.getRuntime().sendText(text);
}
