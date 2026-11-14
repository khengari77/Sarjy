import type { WordleGuess, WordleStats } from './wordleApi';

export type WordleView = 'loading' | 'playing' | 'won' | 'lost' | 'unavailable' | 'error';

/**
 * The single shared Wordle game state. The stage renders it and the voice
 * adapter drives it; neither keeps a second copy.
 */
export interface WordleGameState {
  open: boolean;
  view: WordleView;
  puzzleDate: string;
  puzzleTimezone?: string;
  /** Present only while the game is terminal; the stage and voice never read it earlier. */
  answer?: string;
  guesses: WordleGuess[];
  attemptsRemaining?: number;
  draft: string;
  notice: string;
  announcement: string;
  stats: WordleStats | null;
  statsUnavailable: boolean;
  busy: boolean;
  revealedRow: number;
  loaded: boolean;
  /** Last reply produced by the voice adapter, for the visible voice status line. */
  voiceReply: string;
}

export type GuessOutcome =
  { status: 'ok'; guess: WordleGuess } | { status: 'busy' } | { status: 'error'; code: string };

/**
 * The narrow contract the voice adapter needs. It is deliberately free of
 * Svelte runes so command tests can exercise it with a fake.
 */
export interface WordleGamePort {
  readonly state: WordleGameState;
  open(): void;
  close(): void;
  ensureLoaded(): Promise<void>;
  load(): Promise<void>;
  guessWord(word: string, requestId: string): Promise<GuessOutcome>;
}
