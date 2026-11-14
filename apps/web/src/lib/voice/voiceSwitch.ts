import type { VoiceControlState } from '../api';

/**
 * Selected-for-next-call voice and confirmed-active worker voice are distinct.
 * Nothing here is claimed active until the worker ACK names this exact request,
 * voice and settings revision.
 */
export interface VoiceSwitchState {
  pendingRequestId: string;
  pendingVoiceId: string;
  pendingVoiceName: string;
  pendingRevision: number;
  activeVoiceId: string;
  activeVoiceName: string;
  status: 'idle' | 'pending' | 'failed';
  errorCode: string;
}

export interface VoiceSwitchStart {
  requestId: string;
  voiceId: string;
  voiceName: string;
  revision: number;
}

export type VoiceAckOutcome = 'applied' | 'failed' | 'ignored';

export function initialVoiceSwitchState(activeVoiceId = '', activeVoiceName = ''): VoiceSwitchState {
  return {
    pendingRequestId: '',
    pendingVoiceId: '',
    pendingVoiceName: '',
    pendingRevision: 0,
    activeVoiceId,
    activeVoiceName,
    status: 'idle',
    errorCode: '',
  };
}

export function beginVoiceSwitch(state: VoiceSwitchState, start: VoiceSwitchStart): void {
  state.pendingRequestId = start.requestId;
  state.pendingVoiceId = start.voiceId;
  state.pendingVoiceName = start.voiceName;
  state.pendingRevision = start.revision;
  state.status = 'pending';
  state.errorCode = '';
}

/** Apply one polled control state, fencing stale and duplicate acknowledgements. */
export function acceptVoiceControl(state: VoiceSwitchState, ack: VoiceControlState): VoiceAckOutcome {
  if (state.status !== 'pending') return 'ignored';
  // A late ACK for an older request (or one already replaced) is stale.
  if (!state.pendingRequestId || ack.request_id !== state.pendingRequestId) return 'ignored';
  if (ack.status === 'superseded') {
    failVoiceSwitch(state, 'VOICE_SWITCH_SUPERSEDED');
    return 'failed';
  }
  if (ack.status === 'failed') {
    failVoiceSwitch(state, ack.error_code || 'VOICE_SWITCH_FAILED');
    return 'failed';
  }
  if (ack.status !== 'applied') return 'ignored';
  // Never confirm a different voice or revision than the one requested.
  if (ack.voice_id !== state.pendingVoiceId) return 'ignored';
  if (ack.settings_revision !== state.pendingRevision) return 'ignored';
  if (ack.active_voice_id !== state.pendingVoiceId) return 'ignored';
  if (ack.active_settings_revision < state.pendingRevision) return 'ignored';
  state.activeVoiceId = state.pendingVoiceId;
  state.activeVoiceName = state.pendingVoiceName;
  state.status = 'idle';
  state.errorCode = '';
  state.pendingRequestId = '';
  return 'applied';
}

export function failVoiceSwitch(state: VoiceSwitchState, errorCode: string): void {
  state.status = 'failed';
  state.errorCode = errorCode;
  state.pendingRequestId = '';
}

/** A new call or reconnect resets live state to the worker's session voice. */
export function resetVoiceSwitch(state: VoiceSwitchState, voiceId: string, voiceName: string): void {
  state.pendingRequestId = '';
  state.pendingVoiceId = '';
  state.pendingVoiceName = '';
  state.pendingRevision = 0;
  state.activeVoiceId = voiceId;
  state.activeVoiceName = voiceName;
  state.status = 'idle';
  state.errorCode = '';
}

export interface VoiceAckPollOptions {
  fetchState: () => Promise<VoiceControlState>;
  sleep?: (milliseconds: number) => Promise<void>;
  attempts?: number;
  intervalMs?: number;
  isCurrent?: () => boolean;
}

/**
 * Poll the server for the worker ACK. Silence is not a failure: if no next
 * utterance has been synthesized yet the change stays pending, so the UI keeps
 * showing "Updating voice…" rather than falsely confirming or reverting.
 */
export async function awaitVoiceControl(
  state: VoiceSwitchState,
  options: VoiceAckPollOptions,
): Promise<VoiceAckOutcome | 'pending'> {
  const sleep = options.sleep ?? ((milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)));
  const attempts = options.attempts ?? 30;
  const intervalMs = options.intervalMs ?? 400;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (state.status !== 'pending' || options.isCurrent?.() === false) return 'ignored';
    try {
      const ack = await options.fetchState();
      const outcome = acceptVoiceControl(state, ack);
      if (outcome !== 'ignored') return outcome;
    } catch {
      // Transient read failure: keep polling while the request is still pending.
    }
    if (attempt + 1 < attempts) await sleep(intervalMs);
  }
  return state.status === 'pending' ? 'pending' : state.status === 'failed' ? 'failed' : 'applied';
}
