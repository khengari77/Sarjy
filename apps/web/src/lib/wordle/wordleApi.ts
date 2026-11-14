import { request } from '../api';

/** Server-owned letter mark. The UI never derives marks from the answer. */
export type Mark = 'correct' | 'present' | 'absent';
export type GameStatus = 'in_progress' | 'won' | 'lost';

export const MAX_ATTEMPTS = 6;

export interface WordleGuess {
  word: string;
  marks: Mark[];
}

export interface WordleGame {
  id?: string;
  guesses: WordleGuess[];
  status: GameStatus;
  /** Server-reported remaining attempts; derived from stored guesses when absent. */
  attemptsRemaining: number;
}

export interface WordleToday {
  date: string;
  available: boolean;
  game: WordleGame | null;
  /** Present only when the game is terminal; never rendered before then. */
  answer?: string;
  /** Server reason for an unavailable puzzle (e.g. `WORDLE_DISABLED`). */
  code?: string;
  /** Server puzzle timezone when the contract exposes one. */
  timezone?: string;
}

export interface WordleGuessResult {
  marks: Mark[];
  status: GameStatus;
  answer?: string;
  game?: WordleGame;
}

export interface WordleStats {
  played: number;
  won: number;
  current_streak: number;
  max_streak: number;
}

export type RequestFn = (path: string, body?: unknown) => Promise<unknown>;

const MARK_ALIASES: Record<string, Mark> = {
  correct: 'correct',
  green: 'correct',
  present: 'present',
  yellow: 'present',
  absent: 'absent',
  gray: 'absent',
  grey: 'absent',
};

const STATUSES: GameStatus[] = ['in_progress', 'won', 'lost'];

function invalid(): Error {
  return new Error('WORDLE_RESPONSE_INVALID');
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function mark(value: unknown): Mark | null {
  if (typeof value === 'number') {
    return value === 2 ? 'correct' : value === 1 ? 'present' : value === 0 ? 'absent' : null;
  }
  if (typeof value !== 'string') return null;
  return MARK_ALIASES[value.toLowerCase()] ?? null;
}

/** Accepts string marks, numeric Wordle marks, or `{mark: ...}` entries. */
function marks(value: unknown): Mark[] | null {
  if (!Array.isArray(value) || value.length !== 5) return null;
  const parsed = value.map((entry) => {
    const direct = mark(entry);
    if (direct) return direct;
    const item = record(entry);
    return item ? mark(item.mark ?? item.state ?? item.result) : null;
  });
  if (parsed.some((entry) => entry === null)) return null;
  return parsed as Mark[];
}

function status(value: unknown): GameStatus | null {
  return typeof value === 'string' && STATUSES.includes(value as GameStatus) ? (value as GameStatus) : null;
}

function answer(value: unknown): string | undefined {
  return typeof value === 'string' && /^[a-z]{5}$/i.test(value) ? value.toUpperCase() : undefined;
}

function attempts(value: unknown, guesses: number): number {
  if (typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= MAX_ATTEMPTS) return value;
  // The server always sends stored guesses; the remainder is derived, never invented.
  return Math.max(0, MAX_ATTEMPTS - guesses);
}

function guess(value: unknown): WordleGuess | null {
  const item = record(value);
  if (!item) return null;
  const word =
    typeof item.word === 'string'
      ? item.word.toUpperCase()
      : typeof item.guess === 'string'
        ? item.guess.toUpperCase()
        : '';
  const parsed = marks(item.marks);
  return /^[A-Z]{5}$/.test(word) && parsed ? { word, marks: parsed } : null;
}

function guessList(value: unknown): WordleGuess[] | null {
  if (!Array.isArray(value)) return null;
  const parsed = value.map(guess);
  if (parsed.some((entry) => entry === null)) return null;
  return parsed as WordleGuess[];
}

function game(value: unknown): WordleGame {
  const item = record(value);
  if (!item) throw invalid();
  const guesses = guessList(item.guesses ?? []);
  if (!guesses) throw invalid();
  return {
    id: typeof item.id === 'string' ? item.id : undefined,
    guesses,
    status: status(item.status) ?? 'in_progress',
    attemptsRemaining: attempts(item.attempts_remaining, guesses.length),
  };
}

/** The answer is only ever read out of a terminal game. */
function revealedAnswer(value: unknown, state: GameStatus): string | undefined {
  return state === 'in_progress' ? undefined : answer(value);
}

export function normalizeToday(payload: unknown): WordleToday {
  const item = record(payload);
  if (!item || typeof item.date !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(item.date)) throw invalid();
  const code = typeof item.code === 'string' && /^[A-Z0-9_]+$/.test(item.code) ? item.code : undefined;
  const timezone = typeof item.timezone === 'string' && item.timezone ? item.timezone : undefined;
  if (item.available === false) return { date: item.date, available: false, game: null, code, timezone };
  if (item.available !== true) throw invalid();
  const parsed = game(item.game);
  const sourceAnswer = record(item.game)?.answer ?? item.answer;
  return {
    date: item.date,
    available: true,
    game: parsed,
    answer: revealedAnswer(sourceAnswer, parsed.status),
    timezone,
  };
}

export function normalizeGuessResult(payload: unknown): WordleGuessResult {
  const item = record(payload);
  if (!item) throw invalid();
  // The contract echoes the full game at the top level: status, guesses,
  // attempts_remaining and (only at terminal) answer.
  const echoed = item.guesses === undefined ? undefined : game(item);
  const parsedGame = echoed ?? (item.game === undefined ? undefined : game(item.game));
  const parsedMarks = marks(item.marks) ?? parsedGame?.guesses.at(-1)?.marks;
  const parsedStatus = status(item.status) ?? parsedGame?.status;
  if (!parsedMarks || !parsedStatus) throw invalid();
  const state: GameStatus = parsedStatus;
  const revealed = revealedAnswer(item.answer, state) ?? revealedAnswer(record(item.game)?.answer, state);
  return { marks: parsedMarks, status: state, answer: revealed, game: parsedGame };
}

export function normalizeStats(payload: unknown): WordleStats {
  const item = record(payload);
  if (!item) throw invalid();
  const keys: (keyof WordleStats)[] = ['played', 'won', 'current_streak', 'max_streak'];
  const parsed = {} as WordleStats;
  for (const key of keys) {
    const value = item[key];
    if (typeof value !== 'number' || !Number.isInteger(value) || value < 0) throw invalid();
    parsed[key] = value;
  }
  return parsed;
}

export function createWordleClient(send: RequestFn = request) {
  return {
    async today(): Promise<WordleToday> {
      return normalizeToday(await send('wordle/today'));
    },
    async guess(word: string, requestId: string): Promise<WordleGuessResult> {
      return normalizeGuessResult(await send('wordle/guesses', { guess: word, request_id: requestId }));
    },
    async stats(): Promise<WordleStats> {
      return normalizeStats(await send('wordle/stats'));
    },
  };
}

export type WordleClient = ReturnType<typeof createWordleClient>;
