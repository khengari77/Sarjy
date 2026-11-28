import { describe, expect, it } from 'vitest';
import fixturesFile from '../../../../../packages/contracts/wordle_command_fixtures.json';
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
    ['Guess CRANE', 'CRANE', false],
    ['guess crane', 'CRANE', false],
    ['C R A N E', 'CRANE', true],
    ['guess C R A N E', 'CRANE', true],
    ['guess c-r-a-n-e', 'CRANE', true],
    ['my guess is SLATE', 'SLATE', false],
    ['wordle guess slice please', 'SLICE', false],
    ['enter a guess: plant', 'PLANT', false],
  ])('normalizes a spoken guess: %s', (text, word, spelled) => {
    expect(parseWordleCommand(text, { active: true })).toEqual({ kind: 'guess', word, spelled });
    expect(parseWordleCommand(text)).toBeNull();
  });

  it('asks for a word when the guess is empty', () => {
    expect(parseWordleCommand('guess', { active: true })).toEqual({
      kind: 'ambiguous',
      spoken: 'Which five-letter word should I guess?',
    });
  });

  it.each(['guess CRAN', 'guess CRANES', 'guess CR4NE', 'guess crane please extra'])(
    'rejects a malformed guess: %s',
    (text) => {
      const command = parseWordleCommand(text, { active: true });
      expect(command?.kind).toBe('guess_invalid');
      if (command?.kind === 'guess_invalid') expect(command.spoken).toContain('five-letter word');
    },
  );

  it('clarifies a bare wordle or play instead of mutating', () => {
    expect(parseWordleCommand('wordle')?.kind).toBe('ambiguous');
    expect(parseWordleCommand('play', { active: true })?.kind).toBe('ambiguous');
    expect(parseWordleCommand('play')).toBeNull();
    expect(parseWordleCommand('play the wordle with my friends')?.kind).toBe('ambiguous');
  });

  it.each(['end the call', 'mute the mic', 'new conversation'])(
    'does not swallow %s while the game is open',
    (text) => {
      expect(parseWordleCommand(text, { active: true })).toBeNull();
    },
  );

  it('leaves unrelated speech to the conversation', () => {
    for (const text of [
      "what's the weather",
      'tell me a joke',
      'I guess so',
      'guess what happened today',
      'guess again',
      'try again',
      'show me the answer',
      'Make the voice a woman',
      'Make your voice a lady',
      'make the text darker',
      'Make me laugh',
      'Spell my name',
      'Enter the memory explorer',
      'Submit my feedback',
      "Guess I'll have tea",
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

describe('shared allowlist parity fixtures', () => {
  const fixtures = fixturesFile.cases as { text: string; client: string | null; worker: boolean; active?: boolean }[];

  it.each(fixtures)('parses the shared fixture: $text', ({ text, client, active }) => {
    expect(parseWordleCommand(text, { active: active === true })?.kind ?? null).toBe(client);
  });
});
