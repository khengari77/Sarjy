import type { SessionConfig } from '../api';
import { startVoiceSession } from '../chat/session';
import type { ConnectionDeps, VoiceState } from './connection';
import { errorCode } from './errors';

export async function startSessionAttempt(
  state: VoiceState,
  deps: ConnectionDeps,
  context: {
    microphone: boolean;
    conversationId: string;
    isCurrent(): boolean;
    releaseSession(session: SessionConfig | null): Promise<boolean>;
    clearOwnedLive(sessionId: string): void;
    onSessionCreated?(session: SessionConfig): void;
  },
) {
  const voice = deps.getVoice();
  const config = await startVoiceSession(context.conversationId, deps.getLanguage(), voice.id);
  // Register the server session before awaiting the transport so cancel/end
  // can release it even while runtime.connect() is still pending.
  context.onSessionCreated?.(config);
  // Every startup exit collects the release result; a failed release is logged
  // with the session and the path that abandoned it instead of vanishing.
  const release = async (path: string) => {
    if (await context.releaseSession(config)) return;
    console.warn('[Sarjy] Voice session release failed', { sessionId: config.id, path });
  };

  if (!context.isCurrent()) {
    await release('superseded-before-connect');
    return;
  }

  try {
    state.transportStatus = 'connecting';
    state.agentState = null;
    const runtime = deps.getRuntime();
    await runtime.connect(config);
    if (!context.isCurrent()) {
      await release('superseded-during-connect');
      return;
    }
    state.live = config;
    state.activeVoiceName = voice.name;
    state.muted = !context.microphone;
    if (context.microphone) await runtime.startMicrophone();
    if (!context.isCurrent()) {
      context.clearOwnedLive(config.id);
      await release('superseded-during-microphone');
      return;
    }
    deps.meters.setupMeters();
  } catch (error) {
    if (context.isCurrent()) {
      context.clearOwnedLive(config.id);
      state.muted = true;
      state.activeVoiceName = '';
      state.connectionLost = false;
      state.provisional = null;
      state.transportStatus = 'disconnected';
      state.agentState = null;
      state.voiceErrorCode = errorCode(error);
      await deps
        .getRuntime()
        .disconnect()
        .catch(() => {});
    }
    await release('startup-failed');
  }
}
