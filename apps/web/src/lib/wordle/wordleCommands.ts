import type { WordleGuess } from './wordleApi';
import spec from '../../../../../packages/contracts/wordle_commands.json';

/**
 * Deterministic parser for the allowlisted Wordle voice commands (#110).
 *
 * It runs on **finalized** transcript text only. The caller is responsible for
 * dropping partials; this module stays pure so the same text always produces
 * the same command and never touches network or UI state.
 *
 * The allowlist lives in `packages/contracts/wordle_commands.json`. The voice
 * worker reads the same file to suppress conversational replies, and both
 * suites run `wordle_command_fixtures.json` so the two cannot drift.
 */
export type WordleCommand =
  | { kind: 'play' }
  | { kind: 'guess'; word: string }
  | { kind: 'guess_invalid'; spoken: string }
  | { kind: 'remaining' }
  | { kind: 'guesses' }
  | { kind: 'quit' }
  | { kind: 'reveal' }
  | { kind: 'confirm' }
  | { kind: 'cancel' }
  | { kind: 'ambiguous'; spoken: string };

const INVALID_GUESS_SPOKEN = 'I need a five-letter word. Say guess, then the word, like guess C R A N E.';

const CONVERSATIONAL = new Set(spec.conversational);
const GUESS_PATTERNS = spec.guess_intent_patterns.map((pattern) => new RegExp(pattern));
const SPELLED = new RegExp(spec.spelled_pattern);
const PLAY = new RegExp(spec.exact.play);
const REMAINING = new RegExp(spec.exact.remaining);
const GUESSES = new RegExp(spec.exact.guesses);
const QUIT = new RegExp(spec.exact.quit);
const REVEAL = new RegExp(spec.exact.reveal);
const CONFIRM = new RegExp(spec.exact.confirm);
const CANCEL = new RegExp(spec.exact.cancel);
const AMBIGUOUS_PLAY = new RegExp(spec.ambiguous_play);
const AMBIGUOUS_ACTION = new RegExp(spec.ambiguous_action);

/** Lowercase, fold typographic apostrophes, drop trailing punctuation. */
export function normalizeUtterance(text: string): string {
  return text
    .toLowerCase()
    .replace(/[\u2018\u2019]/g, "'")
    .replace(/[\u201c\u201d]/g, '"')
    .replace(/[,;]+/g, ' ')
    .replace(/[.!?:]+\s*$/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

/** Collapse "C R A N E", "c-r-a-n-e" and "c.r.a.n.e" into one word. */
function collapseSpelledLetters(tail: string): string {
  if (SPELLED.test(tail)) return tail.replace(/[^a-z]/g, '');
  return tail;
}

/** Strip a leading guess intent phrase, returning the remaining word text. */
function guessTail(normalized: string): string | null {
  for (const pattern of GUESS_PATTERNS) {
    const match = pattern.exec(normalized);
    if (match) return normalized.slice(match[0].length).trim();
  }
  return null;
}

function parseGuess(normalized: string): WordleCommand | null {
  const tail = guessTail(normalized);
  if (tail === null) return null;
  const cleaned = tail
    .replace(/\s+(?:please|for me|now)$/, '')
    .replace(/^(?:the|a|word)\s+/, '')
    .trim();
  if (!cleaned) return { kind: 'ambiguous', spoken: 'Which five-letter word should I guess?' };
  // "Guess what happened", "guess again" and similar are conversation, not a
  // malformed WORDLE attempt; only letter-shaped tails are ours to correct.
  const first = cleaned.split(' ')[0].replace(/[^a-z]/g, '');
  if (CONVERSATIONAL.has(first)) return null;
  if (/[^a-z\s.-]/.test(cleaned)) return { kind: 'guess_invalid', spoken: INVALID_GUESS_SPOKEN };
  const word = collapseSpelledLetters(cleaned);
  if (!/^[a-z]{5}$/.test(word)) return { kind: 'guess_invalid', spoken: INVALID_GUESS_SPOKEN };
  return { kind: 'guess', word: word.toUpperCase() };
}

/**
 * Map one finalized utterance to a command, an explicit clarification, or
 * `null` when the text is ordinary conversation (the adapter must not steal
 * unrelated speech).
 */
export function parseWordleCommand(text: string): WordleCommand | null {
  const normalized = normalizeUtterance(text);
  if (!normalized) return null;

  if (CONFIRM.test(normalized)) return { kind: 'confirm' };
  if (CANCEL.test(normalized)) return { kind: 'cancel' };

  const guess = parseGuess(normalized);
  if (guess) return guess;

  // A fully spelled sequence ("C R A N E") is an unambiguous guess even
  // without the word "guess"; a bare word is left to conversation.
  const spellable = normalized.replace(/\s+(?:please|for me)$/, '').trim();
  if (SPELLED.test(spellable)) {
    return { kind: 'guess', word: spellable.replace(/[^a-z]/g, '').toUpperCase() };
  }

  if (PLAY.test(normalized)) return { kind: 'play' };
  if (REMAINING.test(normalized)) return { kind: 'remaining' };
  if (GUESSES.test(normalized)) return { kind: 'guesses' };
  if (QUIT.test(normalized)) return { kind: 'quit' };
  if (REVEAL.test(normalized)) return { kind: 'reveal' };

  if (AMBIGUOUS_PLAY.test(normalized)) {
    return { kind: 'ambiguous', spoken: "Did you want to play today's Wordle?" };
  }
  if (AMBIGUOUS_ACTION.test(normalized)) {
    return {
      kind: 'ambiguous',
      spoken: 'I can play Wordle, take a guess, read your guesses, or quit. What would you like?',
    };
  }
  return null;
}

/**
 * A stable request id for a spoken guess. The server treats `request_id` as the
 * replay key, so the same word on the same puzzle always maps to the same id;
 * a changed word (or a new day) maps to a new one.
 */
export function guessRequestId(scope: string, puzzleDate: string, word: string): string {
  return `${scope}-${puzzleDate}-${word.toUpperCase()}`;
}

export function alreadyGuessed(guesses: WordleGuess[], word: string): boolean {
  return guesses.some((guess) => guess.word === word.toUpperCase());
}
