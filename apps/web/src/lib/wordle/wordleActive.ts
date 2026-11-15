import type { VoiceEvent } from '../audio/voice-runtime';

/**
 * Keeps the worker's `wordle.active` flag in step with the open game. The room
 * may not exist (or the worker may not have joined) when the game opens, so the
 * current value is re-sent whenever the transport or the agent reports a status.
 */
export function createActiveSync(send: (event: Record<string, unknown>) => Promise<void>, isOpen: () => boolean) {
  function publish(): void {
    void send({ type: 'wordle.active', value: isOpen() }).catch(() => {});
  }
  return {
    publish,
    handleEvent(event: VoiceEvent): void {
      if (event.type === 'status' && event.text !== 'disconnected' && event.text !== 'reconnecting') publish();
    },
  };
}
