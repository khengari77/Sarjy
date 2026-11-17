import type { GameStatus, WordleGuessResult, WordleToday } from './wordleApi';
import type { WordleGameState } from './wordleGamePort';
import { describeGuess, friendlyError, statusText } from './wordleState';

export function remainingFor(state: WordleGameState): number {
  return state.attemptsRemaining ?? Math.max(0, 6 - state.guesses.length);
}

export function statusLineFor(state: WordleGameState): string {
  const status: GameStatus = state.view === 'won' || state.view === 'lost' ? state.view : 'in_progress';
  return statusText(status, state.guesses.length, state.answer, remainingFor(state));
}

export function applyToday(state: WordleGameState, today: WordleToday): void {
  state.puzzleDate = today.date;
  state.puzzleTimezone = today.timezone;
  state.answer = today.answer;
  state.guesses = today.game?.guesses ?? [];
  state.attemptsRemaining = today.game?.attemptsRemaining;
  state.revealedRow = -1;
  state.draft = '';
  if (!today.available) {
    state.view = 'unavailable';
    state.notice = friendlyError(today.code ?? 'WORDLE_UNAVAILABLE');
    state.loaded = true;
    return;
  }
  const status: GameStatus = today.game?.status ?? 'in_progress';
  state.view = status === 'in_progress' ? 'playing' : status;
  state.loaded = true;
  const loaded = status === 'in_progress' ? 'Puzzle loaded.' : '';
  state.announcement = [loaded, statusLineFor(state)].filter(Boolean).join(' ');
}

export function applyResult(state: WordleGameState, word: string, result: WordleGuessResult): void {
  const next = result.game?.guesses ?? [...state.guesses, { word, marks: result.marks }];
  const status = result.game?.status ?? result.status;
  state.guesses = next;
  state.attemptsRemaining = result.game?.attemptsRemaining;
  state.view = status === 'in_progress' ? 'playing' : status;
  // The server only sends an answer for a terminal game; normalizeGuessResult
  // already drops one supplied earlier.
  state.answer = result.answer ?? state.answer;
  state.revealedRow = next.length - 1;
  state.draft = '';
  const last = next[state.revealedRow];
  const spoken = last && last.word === word ? describeGuess(last) : `Posted ${word}.`;
  state.announcement = [spoken, statusLineFor(state)].filter(Boolean).join(' ');
}
