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
    releaseSession(session: SessionConfig | null): Promise<void>;
    clearOwnedLive(sessionId: string): void;
  },
) {
  const voice = deps.getVoice();
  const config = await startVoiceSession(context.conversationId, deps.getLanguage(), voice.id);
  if (!context.isCurrent()) {
    await context.releaseSession(config);
    return;
  }

  try {
    state.transportStatus = 'connecting';
    state.agentState = null;
    const runtime = deps.getRuntime();
    await runtime.connect(config);
    if (!context.isCurrent()) {
      await context.releaseSession(config);
      return;
    }
    state.live = config;
    state.activeVoiceName = voice.name;
    state.muted = !context.microphone;
    if (context.microphone) await runtime.startMicrophone();
    if (!context.isCurrent()) {
      context.clearOwnedLive(config.id);
      await context.releaseSession(config);
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
    await context.releaseSession(config);
  }
}
