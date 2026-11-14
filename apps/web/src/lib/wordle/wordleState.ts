import { MAX_ATTEMPTS, type GameStatus, type Mark, type WordleGuess } from './wordleApi';

export const WORD_LENGTH = 5;
export { MAX_ATTEMPTS };
/**
 * The API contract does not echo its configured timezone yet; this mirrors the
 * documented backend default so the countdown is never presented as local time.
 */
export const WORDLE_TIMEZONE = 'Africa/Tripoli';

export const KEYBOARD_ROWS: string[][] = [
  ['Q', 'W', 'E', 'R', 'T', 'Y', 'U', 'I', 'O', 'P'],
  ['A', 'S', 'D', 'F', 'G', 'H', 'J', 'K', 'L'],
  ['ENTER', 'Z', 'X', 'C', 'V', 'B', 'N', 'M', 'BACKSPACE'],
];

export type RowState = 'empty' | 'draft' | 'revealed';

export interface Tile {
  letter: string;
  mark: Mark | null;
  column: number;
}

export interface BoardRow {
  index: number;
  state: RowState;
  tiles: Tile[];
}

const MARK_RANK: Record<Mark, number> = { absent: 1, present: 2, correct: 3 };

export function buildBoard(guesses: WordleGuess[], draft: string, accepting: boolean): BoardRow[] {
  return Array.from({ length: MAX_ATTEMPTS }, (_, index) => {
    const guess = guesses[index];
    if (guess) {
      return {
        index,
        state: 'revealed' as const,
        tiles: Array.from({ length: WORD_LENGTH }, (_, column) => ({
          letter: guess.word[column] ?? '',
          mark: guess.marks[column] ?? null,
          column,
        })),
      };
    }
    if (index === guesses.length && accepting) {
      return {
        index,
        state: 'draft' as const,
        tiles: Array.from({ length: WORD_LENGTH }, (_, column) => ({
          letter: draft[column] ?? '',
          mark: null,
          column,
        })),
      };
    }
    return {
      index,
      state: 'empty' as const,
      tiles: Array.from({ length: WORD_LENGTH }, (_, column) => ({ letter: '', mark: null, column })),
    };
  });
}

/** Best-known mark per letter; correct beats present beats absent. */
export function keyboardMarks(guesses: WordleGuess[]): Record<string, Mark> {
  const result: Record<string, Mark> = {};
  for (const guess of guesses) {
    for (let index = 0; index < guess.word.length; index += 1) {
      const letter = guess.word[index];
      const mark = guess.marks[index];
      if (!letter || !mark) continue;
      if (!result[letter] || MARK_RANK[mark] > MARK_RANK[result[letter]]) result[letter] = mark;
    }
  }
  return result;
}

export function tileLabel(tile: Tile, rowIndex: number): string {
  const position = `row ${rowIndex + 1}, column ${tile.column + 1}`;
  if (!tile.letter) return `${position}, empty`;
  if (!tile.mark) return `${tile.letter}, ${position}, not submitted`;
  if (tile.mark === 'correct') return `${tile.letter}, correct position`;
  if (tile.mark === 'present') return `${tile.letter}, in the word, wrong position`;
  return `${tile.letter}, not in the word`;
}

export function markSymbol(mark: Mark): string {
  return mark === 'correct' ? '✓' : mark === 'present' ? '≈' : '×';
}

export function describeGuess(guess: WordleGuess): string {
  const parts = guess.word.split('').map((letter, index) => {
    const mark = guess.marks[index];
    if (mark === 'correct') return `${letter} correct position`;
    if (mark === 'present') return `${letter} in the word, wrong position`;
    return `${letter} not in the word`;
  });
  return `${guess.word}: ${parts.join('; ')}`;
}

export function statusText(status: GameStatus, attempts: number, answer?: string, attemptsRemaining?: number): string {
  if (status === 'won') return `You won in ${attempts} ${attempts === 1 ? 'guess' : 'guesses'}!`;
  if (status === 'lost') return answer ? `Out of guesses. The answer was ${answer}.` : 'Out of guesses.';
  const left = attemptsRemaining ?? Math.max(0, MAX_ATTEMPTS - attempts);
  return `${left} ${left === 1 ? 'guess' : 'guesses'} left.`;
}

export function friendlyError(code: string): string {
  switch (code) {
    case 'WORDLE_DISABLED':
      return 'Wordle is turned off in this build.';
    case 'WORDLE_TIMEZONE_INVALID':
    case 'WORDLE_UNAVAILABLE':
      return "Today's Wordle isn't available right now.";
    case 'WORDLE_GUESS_INVALID':
    case 'INVALID_WORD':
    case 'INVALID_GUESS':
      return 'Enter a five-letter word using A–Z letters.';
    case 'ALREADY_GUESSED':
      return "You've already guessed that word.";
    case 'WORDLE_GAME_OVER':
    case 'NO_ATTEMPTS':
      return 'This game is already over. Reload today’s puzzle to check the result.';
    case 'WORDLE_RATE_LIMITED':
    case 'RATE_LIMITED':
      return 'Too many guesses just now. Try again in a moment.';
    case 'WORDLE_REQUEST_CONFLICT':
    case 'WORDLE_GUESS_CONFLICT':
      return 'That guess did not save cleanly. Reload today’s puzzle and check your board.';
    case 'WORDLE_RESPONSE_INVALID':
      return 'The puzzle response could not be read. Try again.';
    case 'HTTP_401':
    case 'HTTP_403':
      return 'Your session is not available. Reload the page and try again.';
    default:
      return "That didn't go through. Try again.";
  }
}

export function formatPuzzleDate(date: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
  if (!match) return date;
  const value = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]), 12));
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: 'UTC',
    weekday: 'long',
    day: 'numeric',
    month: 'long',
  }).format(value);
}

interface WallClock {
  hour: number;
  minute: number;
  second: number;
}

function wallClock(now: Date, timeZone: string): WallClock {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone,
    hourCycle: 'h23',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).formatToParts(now);
  const pick = (type: string) => Number(parts.find((part) => part.type === type)?.value ?? 0);
  return { hour: pick('hour'), minute: pick('minute'), second: pick('second') };
}

/** Milliseconds until the next midnight in the puzzle timezone. */
export function nextPuzzleCountdown(now: Date, timeZone = WORDLE_TIMEZONE): number {
  const { hour, minute, second } = wallClock(now, timeZone);
  const elapsed = (hour * 3600 + minute * 60 + second) * 1000 + now.getMilliseconds();
  return Math.max(0, 24 * 3600 * 1000 - elapsed);
}

export function formatCountdown(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  return [hours, minutes, seconds].map((part) => String(part).padStart(2, '0')).join(':');
}

/** Idempotency key for a guess submission. */
export function requestId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  return `wordle-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
