import { describe, expect, it } from 'vitest';
import type { WordleGuess } from './wordleApi';
import {
  buildBoard,
  describeGuess,
  formatCountdown,
  formatPuzzleDate,
  friendlyError,
  keyboardMarks,
  nextPuzzleCountdown,
  statusText,
  tileLabel,
} from './wordleState';

const guesses: WordleGuess[] = [
  { word: 'CRANE', marks: ['absent', 'present', 'absent', 'absent', 'correct'] },
  { word: 'ALLEY', marks: ['correct', 'present', 'absent', 'present', 'absent'] },
];

describe('buildBoard', () => {
  it('renders revealed rows, one draft row, then empty rows', () => {
    const board = buildBoard(guesses, 'ST', true);
    expect(board).toHaveLength(6);
    expect(board[0].state).toBe('revealed');
    expect(board[0].tiles[4]).toEqual({ letter: 'E', mark: 'correct', column: 4 });
    expect(board[1].state).toBe('revealed');
    expect(board[2].state).toBe('draft');
    expect(board[2].tiles.map((tile) => tile.letter)).toEqual(['S', 'T', '', '', '']);
    expect(board[3].state).toBe('empty');
    expect(board[3].tiles.every((tile) => tile.mark === null)).toBe(true);
  });

  it('does not reveal a draft row when the game is terminal', () => {
    const board = buildBoard(guesses, 'STA', false);
    expect(board[2].state).toBe('empty');
    expect(board[2].tiles[0].letter).toBe('');
  });

  it('never invents marks beyond what the server returned', () => {
    const board = buildBoard(
      [{ word: 'ALLEY', marks: ['correct', 'present', 'absent', 'present', 'absent'] }],
      '',
      true,
    );
    expect(board[0].tiles.map((tile) => tile.mark)).toEqual(['correct', 'present', 'absent', 'present', 'absent']);
  });
});

describe('keyboardMarks', () => {
  it('keeps the best mark for duplicate letters and ignores unguessed letters', () => {
    const marks = keyboardMarks(guesses);
    expect(marks.A).toBe('correct');
    expect(marks.L).toBe('present');
    expect(marks.E).toBe('correct');
    expect(marks.Z).toBeUndefined();
  });
});

describe('accessible labels', () => {
  it('names position and state without relying on colour', () => {
    expect(tileLabel({ letter: 'R', mark: 'correct', column: 0 }, 0)).toBe('R, correct position');
    expect(tileLabel({ letter: 'A', mark: 'present', column: 1 }, 0)).toBe('A, in the word, wrong position');
    expect(tileLabel({ letter: 'N', mark: 'absent', column: 2 }, 0)).toBe('N, not in the word');
    expect(tileLabel({ letter: '', mark: null, column: 3 }, 0)).toBe('row 1, column 4, empty');
  });

  it('describes every letter of a guess for announcements', () => {
    expect(describeGuess(guesses[0])).toBe(
      'CRANE: C not in the word; R in the word, wrong position; A not in the word; N not in the word; E correct position',
    );
  });
});

describe('statusText', () => {
  it('reports attempts left while playing', () => {
    expect(statusText('in_progress', 1)).toBe('5 guesses left.');
    expect(statusText('in_progress', 5)).toBe('1 guess left.');
  });

  it('prefers the server attempts remaining', () => {
    expect(statusText('in_progress', 1, undefined, 3)).toBe('3 guesses left.');
    expect(statusText('in_progress', 3, undefined, 0)).toBe('0 guesses left.');
  });

  it('shows the answer only for a terminal loss', () => {
    expect(statusText('won', 3)).toBe('You won in 3 guesses!');
    expect(statusText('lost', 6, 'PLANT')).toBe('Out of guesses. The answer was PLANT.');
    expect(statusText('lost', 6)).toBe('Out of guesses.');
  });
});

describe('dates and countdown', () => {
  it('formats the puzzle date as a calendar day', () => {
    expect(formatPuzzleDate('2026-11-13')).toBe('Friday 13 November');
  });

  it('counts to the next midnight in the puzzle timezone', () => {
    const now = new Date('2026-11-13T21:30:00.000Z');
    expect(nextPuzzleCountdown(now, 'Africa/Tripoli')).toBe(30 * 60 * 1000);
  });

  it('formats countdown values', () => {
    expect(formatCountdown(3_723_000)).toBe('01:02:03');
    expect(formatCountdown(-5)).toBe('00:00:00');
  });
});

describe('friendlyError', () => {
  it('explains unavailable and guess errors without leaking internals', () => {
    expect(friendlyError('WORDLE_UNAVAILABLE')).toContain("isn't available");
    expect(friendlyError('WORDLE_DISABLED')).toContain('turned off');
    expect(friendlyError('WORDLE_GUESS_INVALID')).toContain('five-letter');
    expect(friendlyError('WORDLE_RATE_LIMITED')).toContain('Too many');
    expect(friendlyError('WORDLE_GAME_OVER')).toContain('already over');
    expect(friendlyError('SOMETHING_ELSE')).toBe("That didn't go through. Try again.");
  });
});
