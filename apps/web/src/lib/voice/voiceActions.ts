import type { VoiceRuntime } from '../audio/voice-runtime';
import type { VoiceConnection } from './connection';

export interface VoiceActionDeps {
  live(): boolean;
  connecting(): boolean;
  muted(): boolean;
  canInterrupt(): boolean;
  conversationReady(): boolean;
  getConnection(): VoiceConnection;
  getRuntime(): VoiceRuntime | undefined;
  setAudioBlocked(value: boolean): void;
  run(work: () => Promise<void>, onError?: (error: unknown) => void): Promise<void>;
  onError(error: unknown): void;
}

/** Call controls shared by the orb, buttons and composer. */
export function createVoiceActions(deps: VoiceActionDeps) {
  async function toggleMute() {
    await deps.run(() => deps.getConnection().toggleMute(), deps.onError);
  }

  async function interrupt() {
    if (!deps.live() || !deps.canInterrupt()) return;
    await deps.run(() => deps.getConnection().interrupt(), deps.onError);
  }

  function startConnect() {
    void deps.getConnection().connect(true).catch(deps.onError);
  }

  function handleOrb() {
    if (deps.live()) {
      if (deps.muted()) void toggleMute();
      else if (deps.canInterrupt()) void interrupt();
      return;
    }
    if (!deps.connecting()) startConnect();
  }

  async function sendText(text: string, clear: () => void) {
    if (!text.trim()) return;
    await deps.run(async () => {
      if (!deps.live()) await deps.getConnection().connect(false);
      if (!deps.live()) return;
      await deps.getConnection().sendText(text.trim());
      clear();
    }, deps.onError);
  }

  function sendDraft(text: string, clear: () => void) {
    if (!deps.conversationReady() && !deps.live()) return;
    void sendText(text, clear);
  }

  async function enableAudio() {
    await deps.run(async () => {
      await deps.getRuntime()?.resumeAudio?.();
      deps.setAudioBlocked(false);
    }, deps.onError);
  }

  return { handleOrb, startConnect, toggleMute, interrupt, sendDraft, enableAudio };
}
