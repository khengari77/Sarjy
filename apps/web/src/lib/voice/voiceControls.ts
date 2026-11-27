import type { ConnectionDeps, VoiceState } from './connection';

export async function toggleVoiceMute(state: VoiceState, deps: ConnectionDeps) {
  if (!state.live) return;
  if (state.muted) {
    await deps.getRuntime().startMicrophone();
    state.muted = false;
    deps.meters.startMicMeter();
  } else {
    await deps.getRuntime().stopMicrophone();
    state.muted = true;
    deps.meters.stopMicMeter();
  }
  state.voiceErrorCode = '';
}

export async function interruptVoice(state: VoiceState, deps: ConnectionDeps) {
  await deps.getRuntime().interrupt();
  state.agentState = 'listening';
  state.voiceErrorCode = '';
}

/** Retry the microphone without tearing down a live call. */
export async function retryVoiceMicrophone(state: VoiceState, deps: ConnectionDeps) {
  if (!state.live) return;
  await deps.getRuntime().startMicrophone();
  state.muted = false;
  deps.meters.startMicMeter();
  state.voiceErrorCode = '';
}

export async function sendVoiceText(state: VoiceState, deps: ConnectionDeps, text: string) {
  if (!state.live) return;
  await deps.getRuntime().sendText(text);
}
