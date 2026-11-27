import { friendlyVoiceError, phaseFromAgentState, phaseFromTransportStatus } from '../audio/livekitAdapter';
import {
  deriveOrbPresentation,
  INITIAL_STATE,
  type OrbOverlay,
  type OrbPresentation,
  type VoicePhase,
} from '../orb/orbState';
import { PHASE_LABELS, RECONNECTING_LABEL } from '../orb/labels';
import type { ThemeName } from '../orb/palette';
import type { VoiceState } from './connection';
import { isFatalVoiceError } from './errors';

export interface StageView {
  phase: VoicePhase;
  canInterrupt: boolean;
  errorText: string;
  presentation: OrbPresentation;
}

/**
 * Pure projection from connection state to stage copy, phase and orb
 * presentation. `theme` is the shell's resolved theme (presentation only).
 */
export function deriveStage(voice: VoiceState, arabic: boolean, overlay: OrbOverlay, theme: ThemeName): StageView {
  // A lost connection outranks whatever the agent or transport last reported:
  // otherwise the orb keeps its previous colour while the UI says "Connection
  // lost". `disconnected` owns the offline palette and the truthful label.
  const phase = voice.connectionLost
    ? 'disconnected'
    : ((voice.agentState ? phaseFromAgentState(voice.agentState) : null) ??
      phaseFromTransportStatus(voice.transportStatus) ??
      'idle');
  // A fatal error takes the error state (red) and offers retry. A recoverable
  // error is only a notice: the live phase keeps its own overlay and actions,
  // so the orb cannot stay red (and offer reconnect) for the rest of the call.
  const hasError = voice.voiceErrorCode !== '';
  const hasFatalError = hasError && isFatalVoiceError(voice.voiceErrorCode);
  const errorOverlay = hasError && (!voice.live || hasFatalError);
  const presentation = deriveOrbPresentation(
    {
      ...INITIAL_STATE,
      phase,
      overlay: overlay !== 'none' ? overlay : errorOverlay ? 'error' : voice.live && voice.muted ? 'muted' : 'none',
      micActive: voice.live !== null && !voice.muted,
      sessionId: voice.live?.id ?? null,
      connectionReady: voice.live !== null && !voice.connectionLost,
      micLevel: voice.micLevel,
      outputLevel: voice.outputLevel,
    },
    theme,
  );
  // The page live region reads this label, so it must match the visible stage
  // wording instead of saying "Connecting…" or "Something went wrong".
  const statusLabel = voice.connectionLost
    ? PHASE_LABELS.disconnected
    : voice.live && voice.transportStatus === 'reconnecting'
      ? RECONNECTING_LABEL
      : presentation.statusLabel;
  return {
    phase,
    canInterrupt: phase === 'thinking' || phase === 'speaking',
    errorText: voice.voiceErrorCode ? friendlyVoiceError(voice.voiceErrorCode, arabic) : '',
    presentation: statusLabel === presentation.statusLabel ? presentation : { ...presentation, statusLabel },
  };
}
