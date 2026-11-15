import type { VoiceEvent } from '../audio/voice-runtime';
import type { WordleGamePort } from './wordleGamePort';

/**
 * Keeps the worker's `wordle.active` flag in step with the open game. The room
 * may not exist (or the worker may not have joined) when the game opens, so the
 * current value is re-sent whenever the transport or the agent reports a status.
 */
export function createActiveSync(send: (event: Record<string, unknown>) => Promise<void>, isOpen: () => boolean) {
  function publish(): void {
    try {
      void send({ type: 'wordle.active', value: isOpen() }).catch(() => {});
    } catch {
      // A runtime without control support (or a torn-down one) must never break the page.
    }
  }
  return {
    publish,
    handleEvent(event: VoiceEvent): void {
      if (event.type === 'status' && event.text !== 'disconnected' && event.text !== 'reconnecting') publish();
    },
  };
}

/**
 * The model's Wordle tools changed the server game: show the popup (opening it
 * if needed) and re-read the authoritative board in place, exactly as if the
 * player had typed the guess.
 */
export function applyWordleToolEvent(
  game: Pick<WordleGamePort, 'open' | 'close' | 'refresh' | 'state'>,
  event: VoiceEvent,
): void {
  if (event.type !== 'wordle') return;
  if (event.action === 'close') {
    game.close();
    return;
  }
  if (event.action === 'open' && !game.state.open) game.open();
  void game.refresh();
}
