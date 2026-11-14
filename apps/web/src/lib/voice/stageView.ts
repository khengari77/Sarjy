import {
  friendlyVoiceError,
  isFatalVoiceError,
  phaseFromAgentState,
  phaseFromTransportStatus,
} from '../audio/livekitAdapter';
import {
  deriveOrbPresentation,
  INITIAL_STATE,
  type OrbOverlay,
  type OrbPresentation,
  type VoicePhase,
} from '../orb/orbState';
import type { VoiceState } from './connection';

export interface StageView {
  phase: VoicePhase;
  canInterrupt: boolean;
  errorText: string;
  presentation: OrbPresentation;
}

/** Pure projection from connection state to stage copy, phase and orb presentation. */
export function deriveStage(voice: VoiceState, arabic: boolean, overlay: OrbOverlay): StageView {
  const phase =
    (voice.agentState ? phaseFromAgentState(voice.agentState) : null) ??
    phaseFromTransportStatus(voice.transportStatus) ??
    'idle';
  const fatalError = voice.voiceErrorCode !== '' && isFatalVoiceError(voice.voiceErrorCode);
  return {
    phase,
    canInterrupt: phase === 'thinking' || phase === 'speaking',
    errorText: voice.voiceErrorCode ? friendlyVoiceError(voice.voiceErrorCode, arabic) : '',
    presentation: deriveOrbPresentation({
      ...INITIAL_STATE,
      phase,
      overlay: overlay !== 'none' ? overlay : fatalError ? 'error' : voice.live && voice.muted ? 'muted' : 'none',
      micActive: voice.live !== null && !voice.muted,
      sessionId: voice.live?.id ?? null,
      connectionReady: voice.live !== null && !voice.connectionLost,
      micLevel: voice.micLevel,
      outputLevel: voice.outputLevel,
    }),
  };
}
