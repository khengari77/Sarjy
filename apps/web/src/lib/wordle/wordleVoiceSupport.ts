import type { VoiceEvent } from '../audio/voice-runtime';
import type { WordleGuess } from './wordleApi';
import type { GuessOutcome, WordleGamePort, WordleGameState } from './wordleGamePort';
import { createWordleVoice, type WordleVoice } from './wordleVoice';

export const ZERO_MARKS: WordleGuess['marks'] = ['absent', 'absent', 'absent', 'absent', 'absent'];

export interface FakeWordle {
  port: WordleGamePort;
  spoken: string[];
  calls: {
    reset: number;
    open: number;
    close: number;
    load: number;
    ensureLoaded: number;
    guesses: { word: string; id: string }[];
  };
  state: WordleGameState;
  guessResult: (word: string, requestId: string) => GuessOutcome;
  /** Word-list answer for checkWord; every word is allowed unless a test says otherwise. */
  allowed: (word: string) => boolean | null;
  pendingFlags: (string | null)[];
}

export function fakeGame(overrides: Partial<WordleGameState> = {}, guess?: FakeWordle['guessResult']): FakeWordle {
  const state: WordleGameState = {
    open: false,
    view: 'playing',
    puzzleDate: '2026-11-14',
    answer: undefined,
    guesses: [],
    attemptsRemaining: 6,
    draft: '',
    notice: '',
    announcement: '',
    stats: null,
    statsUnavailable: false,
    busy: false,
    revealedRow: -1,
    loaded: true,
    voiceReply: '',
    ...overrides,
  };
  const spoken: string[] = [];
  const calls: FakeWordle['calls'] = { reset: 0, open: 0, close: 0, load: 0, ensureLoaded: 0, guesses: [] };
  const fake: FakeWordle = {
    port: {
      state,
      open: () => {
        calls.open += 1;
        state.open = true;
      },
      close: () => {
        calls.close += 1;
        state.open = false;
      },
      ensureLoaded: async () => {
        calls.ensureLoaded += 1;
      },
      load: async () => {
        calls.load += 1;
      },
      refresh: async () => {
        calls.load += 1;
      },
      reset: async () => {
        calls.reset += 1;
        state.guesses = [];
        state.attemptsRemaining = 6;
        state.view = 'playing';
        return true;
      },
      guessWord: async (word: string, requestId: string) => {
        calls.guesses.push({ word, id: requestId });
        const outcome = fake.guessResult(word, requestId);
        if (outcome.status === 'ok') state.guesses = [...state.guesses, outcome.guess];
        return outcome;
      },
      checkWord: async (word: string) => fake.allowed(word),
    },
    spoken,
    calls,
    state,
    guessResult: guess ?? ((word) => ({ status: 'ok', guess: { word, marks: ZERO_MARKS } })),
    allowed: () => true,
    pendingFlags: [],
  };
  return fake;
}

export function setup(
  overrides: Partial<WordleGameState> = {},
  guess?: FakeWordle['guessResult'],
): { fake: FakeWordle; voice: WordleVoice } {
  const fake = fakeGame({ open: true, ...overrides }, guess);
  const pendingFlags: (string | null)[] = [];
  const voice = createWordleVoice({
    game: fake.port,
    speak: (text) => fake.spoken.push(text),
    scope: () => 'scope1',
    onPending: (kind) => pendingFlags.push(kind),
  });
  fake.pendingFlags = pendingFlags;
  return { fake, voice };
}

export function transcript(text: string, segmentId: string, extra: Partial<VoiceEvent> = {}): VoiceEvent {
  return { type: 'transcript', text, final: true, segmentId, speaker: 'user', epoch: 1, ...extra } as VoiceEvent;
}
