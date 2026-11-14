import { describe, expect, it } from 'vitest';
import type { WordleGuess } from './wordleApi';
import { alreadyGuessed, guessRequestId, normalizeUtterance, parseWordleCommand } from './wordleCommands';

describe('normalizeUtterance', () => {
  it('folds case, apostrophes, punctuation and spacing', () => {
    expect(normalizeUtterance('  GUESS,  C R A N E! ')).toBe('guess c r a n e');
    expect(normalizeUtterance('Play today’s Wordle.')).toBe("play today's wordle");
    expect(normalizeUtterance("What's the Wordle answer?")).toBe("what's the wordle answer");
  });
});

describe('parseWordleCommand', () => {
  it.each([
    ['Play Wordle', 'play'],
    ["Play today's Wordle", 'play'],
    ['open the wordle game', 'play'],
    ['How many guesses left?', 'remaining'],
    ['how many guesses remain', 'remaining'],
    ['how many guesses do I have left', 'remaining'],
    ['What did I guess?', 'guesses'],
    ['show me my guesses', 'guesses'],
    ['Quit Wordle', 'quit'],
    ['back to chat', 'quit'],
    ['Reveal the answer', 'reveal'],
    ["what's the wordle answer", 'reveal'],
  ])('parses %s', (text, kind) => {
    expect(parseWordleCommand(text)?.kind).toBe(kind);
  });

  it.each([
    ['Guess CRANE', 'CRANE'],
    ['guess crane', 'CRANE'],
    ['C R A N E', 'CRANE'],
    ['guess C R A N E', 'CRANE'],
    ['guess c-r-a-n-e', 'CRANE'],
    ['my guess is SLATE', 'SLATE'],
    ['wordle guess slice please', 'SLICE'],
    ['enter a guess: plant', 'PLANT'],
  ])('normalizes a spoken guess: %s', (text, word) => {
    expect(parseWordleCommand(text)).toEqual({ kind: 'guess', word });
  });

  it('asks for a word when the guess is empty', () => {
    expect(parseWordleCommand('guess')).toEqual({
      kind: 'ambiguous',
      spoken: 'Which five-letter word should I guess?',
    });
  });

  it.each(['guess CRAN', 'guess CRANES', 'guess CR4NE', 'guess crane please extra'])(
    'rejects a malformed guess: %s',
    (text) => {
      const command = parseWordleCommand(text);
      expect(command?.kind).toBe('guess_invalid');
      if (command?.kind === 'guess_invalid') expect(command.spoken).toContain('five-letter word');
    },
  );

  it('clarifies a bare wordle or play instead of mutating', () => {
    expect(parseWordleCommand('wordle')?.kind).toBe('ambiguous');
    expect(parseWordleCommand('play')?.kind).toBe('ambiguous');
    expect(parseWordleCommand('play the wordle with my friends')?.kind).toBe('ambiguous');
  });

  it('leaves unrelated speech to the conversation', () => {
    for (const text of [
      "what's the weather",
      'tell me a joke',
      'I guess so',
      'guess what happened today',
      'guess again',
      'try again',
      'show me the answer',
      '',
    ]) {
      expect(parseWordleCommand(text)).toBeNull();
    }
  });

  it('parses yes/no only as confirmation intents', () => {
    expect(parseWordleCommand('Yes')).toEqual({ kind: 'confirm' });
    expect(parseWordleCommand('no, never mind')).toEqual({ kind: 'cancel' });
  });
});

describe('guessRequestId', () => {
  it('is stable for the same scope, date and word', () => {
    const first = guessRequestId('tab1', '2026-11-14', 'crane');
    expect(guessRequestId('tab1', '2026-11-14', 'CRANE')).toBe(first);
    expect(guessRequestId('tab1', '2026-11-14', 'slate')).not.toBe(first);
    expect(guessRequestId('tab2', '2026-11-14', 'CRANE')).not.toBe(first);
    expect(guessRequestId('tab1', '2026-11-15', 'CRANE')).not.toBe(first);
  });
});

describe('alreadyGuessed', () => {
  it('compares case-insensitively against the server board', () => {
    const guesses: WordleGuess[] = [{ word: 'CRANE', marks: ['absent', 'absent', 'absent', 'absent', 'absent'] }];
    expect(alreadyGuessed([...guesses], 'crane')).toBe(true);
    expect(alreadyGuessed([...guesses], 'SLATE')).toBe(false);
  });
});
