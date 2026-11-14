import { getSettings, getVoiceControl, submitVoiceChange, type SessionConfig, type VoiceOption } from '../api';
import { errorCode } from './errors';
import {
  awaitVoiceControl,
  beginVoiceSwitch,
  failVoiceSwitch,
  resetVoiceSwitch,
  type VoiceSwitchState,
} from './voiceSwitch';

export interface LiveVoiceSwitchDeps {
  state: VoiceSwitchState;
  getSession(): SessionConfig | null;
  getConversationId(): string | null;
  getVoiceId(): string;
  getVoices(): VoiceOption[];
  selectVoice(voiceId: string): void;
  remember(): void;
  createRequestId(): string;
}

/**
 * Imperative shell for the live (in-call) voice switch. The pure revision and
 * ACK fencing lives in ``voiceSwitch``; this only sequences authenticated
 * submissions, polling and rollback.
 */
export function createLiveVoiceSwitch(deps: LiveVoiceSwitchDeps) {
  let polling = false;
  let currentSessionId: string | null = null;

  function onSession(sessionId: string | null, activeVoiceName: string) {
    if (sessionId === currentSessionId) return;
    currentSessionId = sessionId;
    const selected = deps.getVoiceId();
    const name = activeVoiceName || deps.getVoices().find((entry) => entry.id === selected)?.name || selected;
    resetVoiceSwitch(deps.state, sessionId ? selected : '', sessionId ? name : '');
  }

  function revert() {
    if (!deps.state.activeVoiceId) return;
    deps.selectVoice(deps.state.activeVoiceId);
    deps.remember();
  }

  async function change(): Promise<void> {
    const session = deps.getSession();
    deps.remember();
    if (!session || deps.state.status === 'pending') return;
    const conversationId = deps.getConversationId();
    const voiceId = deps.getVoiceId();
    if (!voiceId || !conversationId) return;
    const voiceName = deps.getVoices().find((entry) => entry.id === voiceId)?.name || voiceId;
    const sessionId = session.id;
    try {
      const settings = await getSettings();
      const requestId = deps.createRequestId();
      beginVoiceSwitch(deps.state, { requestId, voiceId, voiceName, revision: settings.revision });
      const response = await submitVoiceChange({
        conversationId,
        requestId,
        expectedRevision: settings.revision,
        voiceId,
      });
      const control = response.result?.voice_control;
      if (!control) {
        // No worker for this conversation: the choice is a next-call preference.
        deps.state.status = 'idle';
        deps.state.pendingRequestId = '';
        return;
      }
      deps.state.pendingRevision = control.settings_revision ?? settings.revision;
      if (control.status === 'failed') {
        failVoiceSwitch(deps.state, control.error_code || 'VOICE_CONTROL_UNAVAILABLE');
        revert();
        return;
      }
      const outcome = await awaitVoiceControl(deps.state, {
        fetchState: () => getVoiceControl(sessionId),
        isCurrent: () => deps.getSession()?.id === sessionId,
      });
      if (outcome === 'failed') revert();
    } catch (error) {
      failVoiceSwitch(deps.state, errorCode(error));
      revert();
    }
  }

  /** Retry the ACK read when a turn starts; silence alone is never a failure. */
  async function resume(): Promise<void> {
    const session = deps.getSession();
    if (!session || deps.state.status !== 'pending' || polling) return;
    polling = true;
    const sessionId = session.id;
    try {
      const outcome = await awaitVoiceControl(deps.state, {
        fetchState: () => getVoiceControl(sessionId),
        isCurrent: () => deps.getSession()?.id === sessionId,
      });
      if (outcome === 'failed') revert();
    } catch (error) {
      failVoiceSwitch(deps.state, errorCode(error));
    } finally {
      polling = false;
    }
  }

  return { onSession, change, resume };
}
