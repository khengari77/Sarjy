import { LiveKitVoiceRuntime, type VoiceRuntime } from './voice-runtime';

/**
 * Production creates a real LiveKit runtime. Browser tests inject a fake
 * through `window.__SARJY_VOICE_RUNTIME_FACTORY__` while running the dev
 * server, so lifecycle states can be exercised without a paid room.
 */
export function createVoiceRuntime(): VoiceRuntime {
  if (import.meta.env.DEV) {
    const injected = (globalThis as { __SARJY_VOICE_RUNTIME_FACTORY__?: () => VoiceRuntime })
      .__SARJY_VOICE_RUNTIME_FACTORY__;
    if (typeof injected === 'function') return injected();
  }
  return new LiveKitVoiceRuntime();
}
