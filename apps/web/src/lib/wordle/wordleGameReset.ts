import { errorCode } from '../voice/errors';
import { friendlyError } from './wordleState';
import type { WordleClient } from './wordleApi';
import type { WordleGameState } from './wordleGamePort';
import { applyToday } from './wordleGameApply';

interface ResetHooks {
  bump(): number;
  clearPending(): void;
  loadStats(token: number): unknown;
}

/** Delete today's game on the server and show the fresh empty board, without a reload. */
export async function resetGame(state: WordleGameState, client: WordleClient, hooks: ResetHooks): Promise<boolean> {
  if (state.busy) return false;
  state.busy = true;
  state.notice = '';
  try {
    const today = await client.reset();
    const token = hooks.bump();
    hooks.clearPending();
    state.puzzleDate = today.date;
    state.puzzleTimezone = today.timezone;
    state.loaded = true;
    state.guesses = today.game?.guesses ?? [];
    state.attemptsRemaining = today.game?.attemptsRemaining;
    state.view = today.available ? 'playing' : 'unavailable';
    state.answer = undefined;
    state.revealedRow = -1;
    state.draft = '';
    state.voiceReply = '';
    state.stats = null;
    state.announcement = `Game reset. ${state.attemptsRemaining ?? 6} guesses left.`;
    hooks.loadStats(token);
    return true;
  } catch (error) {
    state.notice = friendlyError(errorCode(error));
    state.announcement = state.notice;
    return false;
  } finally {
    state.busy = false;
  }
}

interface RefreshHooks {
  inflight(): Promise<void> | null;
  load(): Promise<void>;
  token(): number;
  loadStats(token: number): unknown;
}

/** In-place sync after a model tool changed the server game: keeps the board mounted and animates new rows. */
export async function refreshGame(state: WordleGameState, client: WordleClient, hooks: RefreshHooks): Promise<void> {
  // A load already in flight may have read the board before the tool's change; wait, then read again.
  const pending = hooks.inflight();
  if (pending) await pending;
  if (!state.loaded) return hooks.load();
  if (state.busy) return;
  const token = hooks.token();
  try {
    const today = await client.today();
    if (token !== hooks.token()) return;
    const before = state.guesses.length;
    applyToday(state, today);
    if (state.guesses.length > before) state.revealedRow = state.guesses.length - 1;
    if (state.view === 'won' || state.view === 'lost') hooks.loadStats(token);
  } catch {
    // Keep the board the player already sees; the next event retries.
  }
}
