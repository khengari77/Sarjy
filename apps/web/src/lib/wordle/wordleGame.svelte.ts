import { errorCode } from '../voice/errors';
import {
  createWordleClient,
  type GameStatus,
  type WordleClient,
  type WordleGuessResult,
  type WordleToday,
} from './wordleApi';
import type { GuessOutcome, WordleGamePort, WordleGameState } from './wordleGamePort';
import { describeGuess, friendlyError, requestId as newRequestId, statusText } from './wordleState';

export interface WordleGameOptions {
  client?: WordleClient;
  newRequestId?: () => string;
}

export interface WordleGame extends WordleGamePort {
  pressKey(key: string): void;
  submitDraft(): Promise<void>;
  statusLine(): string;
  remaining(): number;
}

function initialState(): WordleGameState {
  return {
    open: false,
    view: 'loading',
    puzzleDate: '',
    puzzleTimezone: undefined,
    answer: undefined,
    guesses: [],
    attemptsRemaining: undefined,
    draft: '',
    notice: '',
    announcement: '',
    stats: null,
    statsUnavailable: false,
    busy: false,
    revealedRow: -1,
    loaded: false,
    voiceReply: '',
  };
}

function remainingFor(state: WordleGameState): number {
  return state.attemptsRemaining ?? Math.max(0, 6 - state.guesses.length);
}

export function statusLineFor(state: WordleGameState): string {
  const status: GameStatus = state.view === 'won' || state.view === 'lost' ? state.view : 'in_progress';
  return statusText(status, state.guesses.length, state.answer, remainingFor(state));
}

function applyToday(state: WordleGameState, today: WordleToday): void {
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

function applyResult(state: WordleGameState, word: string, result: WordleGuessResult): void {
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

interface GuessActions {
  submitDraft(): Promise<void>;
  guessWord(word: string, requestId: string): Promise<GuessOutcome>;
  clearPending(): void;
}

function createGuessActions(
  state: WordleGameState,
  client: WordleClient,
  newId: () => string,
  onTerminal: () => void,
): GuessActions {
  let pendingId: string | null = null;
  let pendingWord: string | null = null;

  function clearPending() {
    pendingId = null;
    pendingWord = null;
  }

  async function submitDraft(): Promise<void> {
    if (state.view !== 'playing' || state.busy) return;
    if (state.draft.length < 5) {
      state.notice = 'Enter a five-letter word.';
      state.announcement = state.notice;
      return;
    }
    const word = state.draft;
    // Reuse an id only for a retry of the same word; a new word is a new guess.
    const requestId = pendingWord === word && pendingId ? pendingId : newId();
    pendingId = requestId;
    pendingWord = word;
    state.busy = true;
    state.notice = '';
    try {
      applyResult(state, word, await client.guess(word, requestId));
      if (state.view !== 'playing') onTerminal();
    } catch (error) {
      // Keep the draft so the player can fix or resend it; the id is only
      // reused while the word is unchanged.
      state.notice = friendlyError(errorCode(error));
      state.announcement = state.notice;
    } finally {
      state.busy = false;
    }
  }

  async function guessWord(word: string, requestId: string): Promise<GuessOutcome> {
    if (state.busy) return { status: 'busy' };
    if (state.view !== 'playing') return { status: 'error', code: 'WORDLE_GAME_OVER' };
    state.busy = true;
    state.notice = '';
    try {
      applyResult(state, word, await client.guess(word, requestId));
      if (state.view !== 'playing') onTerminal();
      const guess = state.guesses.at(-1);
      return guess ? { status: 'ok', guess } : { status: 'error', code: 'WORDLE_RESPONSE_INVALID' };
    } catch (error) {
      const code = errorCode(error);
      state.notice = friendlyError(code);
      state.announcement = state.notice;
      return { status: 'error', code };
    } finally {
      state.busy = false;
    }
  }

  return { submitDraft, guessWord, clearPending };
}

/**
 * Server-owned state for one daily Wordle. The board and the voice adapter read
 * the same object; nothing is scored or stored locally except the draft and the
 * terminal answer the server already released.
 */
export function createWordleGame(options: WordleGameOptions = {}): WordleGame {
  const client = options.client ?? createWordleClient();
  const newId = options.newRequestId ?? newRequestId;

  const state = $state<WordleGameState>(initialState());
  let loadToken = 0;
  let inflight: Promise<void> | null = null;

  async function loadStats(token: number) {
    state.statsUnavailable = false;
    try {
      const value = await client.stats();
      if (token === loadToken) state.stats = value;
    } catch {
      if (token !== loadToken) return;
      state.stats = null;
      state.statsUnavailable = true;
    }
  }

  async function performLoad() {
    const token = ++loadToken;
    state.view = 'loading';
    state.notice = '';
    state.announcement = '';
    state.guesses = [];
    state.stats = null;
    state.statsUnavailable = false;
    state.attemptsRemaining = undefined;
    state.answer = undefined;
    state.loaded = false;
    try {
      const today = await client.today();
      if (token !== loadToken) return;
      applyToday(state, today);
      if (state.loaded && !state.notice) void loadStats(token);
    } catch (error) {
      if (token !== loadToken) return;
      const code = errorCode(error);
      state.view =
        code === 'WORDLE_UNAVAILABLE' || code === 'WORDLE_DISABLED' || code === 'WORDLE_TIMEZONE_INVALID'
          ? 'unavailable'
          : 'error';
      state.notice = friendlyError(code);
      state.loaded = true;
    }
  }

  /** Refresh from the server, always issuing one request. */
  function load(): Promise<void> {
    if (inflight) return inflight;
    inflight = performLoad().finally(() => {
      inflight = null;
    });
    return inflight;
  }

  /** Load once; a stage mount and a voice "play" share the same request. */
  function ensureLoaded(): Promise<void> {
    if (state.loaded && !inflight) return Promise.resolve();
    return load();
  }

  function pressKey(key: string) {
    if (state.view !== 'playing' || state.busy) return;
    if (key === 'ENTER') {
      void actions.submitDraft();
    } else if (key === 'BACKSPACE') {
      if (state.draft) state.draft = state.draft.slice(0, -1);
      actions.clearPending();
      state.notice = '';
    } else if (state.draft.length < 5) {
      state.draft += key.toUpperCase();
      actions.clearPending();
      state.notice = '';
    }
  }

  const actions = createGuessActions(state, client, newId, () => void loadStats(loadToken));

  return {
    state,
    open: () => {
      state.open = true;
    },
    close: () => {
      state.open = false;
    },
    load,
    ensureLoaded,
    guessWord: actions.guessWord,
    pressKey,
    submitDraft: actions.submitDraft,
    statusLine: () => statusLineFor(state),
    remaining: () => remainingFor(state),
  };
}
