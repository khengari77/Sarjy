import type { WordleGuess } from './wordleApi';

/**
 * Deterministic parser for the allowlisted Wordle voice commands (#110).
 *
 * It runs on **finalized** transcript text only. The caller is responsible for
 * dropping partials; this module stays pure so the same text always produces
 * the same command and never touches network or UI state.
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

const CONVERSATIONAL = new Set(['what', 'who', 'why', 'how', 'where', 'when', 'so', 'again', 'it', 'that', 'this']);

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
  if (/^[a-z](?:[\s.-]+[a-z]){4}$/.test(tail)) return tail.replace(/[^a-z]/g, '');
  return tail;
}

/** Strip a leading guess intent phrase, returning the remaining word text. */
function guessTail(normalized: string): string | null {
  const patterns = [
    /^(?:please\s+)?(?:my|the)\s+guess\s+is\s+/,
    /^(?:please\s+)?(?:try\s+)?(?:make|submit|enter|spell)\s+(?:a\s+|my\s+)?(?:guess\s*:?\s*)?/,
    /^(?:please\s+)?wordle\s+guess\s*:?\s*/,
    /^(?:please\s+)?(?:try\s+)?(?:a\s+|my\s+)?guess\s*:?\s*/,
  ];
  for (const pattern of patterns) {
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

const EXACT_PLAY =
  /^(?:please\s+)?(?:(?:hey|ok|okay)\s+)?(?:let'?s\s+)?(?:play|start|open|resume)\s+(?:the\s+|today'?s?\s+|my\s+)?wordle(?:\s+(?:game|puzzle))?(?:\s+please)?$/;
const EXACT_REMAINING =
  /^(?:how many (?:guesses|tries|attempts)(?:\s+(?:do i have|have i got|are))?(?:\s+(?:left|remain|remaining))?|how many (?:guesses|tries|attempts)\s+(?:left|remain|remaining)|(?:guesses|tries|attempts)\s+(?:left|remaining)|what(?:'s| is) my (?:wordle )?(?:status|game status)|wordle status)$/;
const EXACT_GUESSES =
  /^(?:what did i guess|what have i guessed|what(?:'s| is) my guess|what are my guesses|show(?: me)? my guesses|my guesses|what guesses (?:did i make|have i made)|list my guesses)$/;
const EXACT_QUIT =
  /^(?:quit|exit|close|stop|leave)\s+(?:the\s+)?wordle(?:\s+(?:game|please))?$|^(?:back to chat|return to chat|close (?:the )?game)$/;
const EXACT_REVEAL =
  /^(?:reveal|spoil)\s+(?:me\s+)?(?:the\s+)?(?:wordle\s+)?answer(?:\s+please)?$|^(?:show|tell|give)\s+me\s+the\s+wordle\s+answer$|^reveal\s+(?:the\s+)?wordle(?:\s+answer)?$|^what(?:'s| is) the wordle answer$/;
const EXACT_CONFIRM = /^(?:yes|yeah|yep|yup|sure|confirm|i'?m sure|go ahead|do it)$/;
const EXACT_CANCEL =
  /^(?:no|nope|cancel|don'?t|do not|never ?mind|stop|keep it secret|no (?:thanks|thank you|never ?mind))$/;

const AMBIGUOUS_PLAY = /^(?:wordle|play|start|open|game)$/;
const AMBIGUOUS_ACTION = /^(?:play|start|open|resume|quit|exit|close|leave|reveal|stop)\b.*\bwordle\b/;

/**
 * Map one finalized utterance to a command, an explicit clarification, or
 * `null` when the text is ordinary conversation (the adapter must not steal
 * unrelated speech).
 */
export function parseWordleCommand(text: string): WordleCommand | null {
  const normalized = normalizeUtterance(text);
  if (!normalized) return null;

  if (EXACT_CONFIRM.test(normalized)) return { kind: 'confirm' };
  if (EXACT_CANCEL.test(normalized)) return { kind: 'cancel' };

  const guess = parseGuess(normalized);
  if (guess) return guess;

  // A fully spelled sequence ("C R A N E") is an unambiguous guess even
  // without the word "guess"; a bare word is left to conversation.
  const spellable = normalized.replace(/\s+(?:please|for me)$/, '').trim();
  if (/^[a-z](?:[\s.-]+[a-z]){4}$/.test(spellable)) {
    return { kind: 'guess', word: spellable.replace(/[^a-z]/g, '').toUpperCase() };
  }

  if (EXACT_PLAY.test(normalized)) return { kind: 'play' };
  if (EXACT_REMAINING.test(normalized)) return { kind: 'remaining' };
  if (EXACT_GUESSES.test(normalized)) return { kind: 'guesses' };
  if (EXACT_QUIT.test(normalized)) return { kind: 'quit' };
  if (EXACT_REVEAL.test(normalized)) return { kind: 'reveal' };

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
