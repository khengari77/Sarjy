import type { VoiceEvent } from '../audio/voice-runtime';
import { alreadyGuessed, guessRequestId, parseWordleCommand, type WordleCommand } from './wordleCommands';
import type { WordleGamePort, WordleGameState } from './wordleGamePort';
import { describeGuess, friendlyError, statusText } from './wordleState';

export type TranscriptEvent = Extract<VoiceEvent, { type: 'transcript' }>;

export interface WordleVoiceDeps {
  game: WordleGamePort;
  /** Delivers the exact reply to speech. Never the conversational LLM. */
  speak(text: string): void;
  /** Stable per-install scope for guess request ids. */
  scope(): string;
}

export interface VoiceResult {
  handled: boolean;
  command?: WordleCommand['kind'];
  spoken?: string;
  ignored?: 'partial' | 'assistant' | 'duplicate' | 'unrecognized' | 'nothing-pending';
}

interface FinalInput {
  text: string;
  segmentId?: string;
  epoch?: number;
}

type Say = (text: string) => string;

const MAX_SEEN = 200;
const SIX_GUESSES = 6;

/** The only place that may expose an answer: a terminal state the server released. */
export function releasedAnswer(state: WordleGameState): string | undefined {
  return state.view === 'won' || state.view === 'lost' ? state.answer : undefined;
}

function guessesLeft(state: WordleGameState): number {
  return state.attemptsRemaining ?? Math.max(0, SIX_GUESSES - state.guesses.length);
}

function statusLine(state: WordleGameState): string {
  const status = state.view === 'won' || state.view === 'lost' ? state.view : 'in_progress';
  return statusText(status, state.guesses.length, releasedAnswer(state), guessesLeft(state));
}

function unavailableText(state: WordleGameState): string | null {
  if (state.view !== 'unavailable' && state.view !== 'error') return null;
  return state.notice || friendlyError(state.view === 'unavailable' ? 'WORDLE_UNAVAILABLE' : 'NETWORK_ERROR');
}

async function playCommand(deps: WordleVoiceDeps, say: Say): Promise<string> {
  deps.game.open();
  await deps.game.ensureLoaded();
  const state = deps.game.state;
  const unavailable = unavailableText(state);
  if (unavailable) return say(unavailable);
  if (state.view === 'won' || state.view === 'lost') {
    return say(`Today's Wordle is finished. ${statusLine(state)}`);
  }
  return say(
    state.guesses.length === 0
      ? "Today's Wordle is ready. Six guesses. Say guess, then a five-letter word."
      : `Back to today's Wordle. ${statusLine(state)}`,
  );
}

async function guessCommand(deps: WordleVoiceDeps, say: Say, word: string): Promise<string> {
  await deps.game.ensureLoaded();
  const state = deps.game.state;
  const unavailable = unavailableText(state);
  if (unavailable) return say(unavailable);
  if (state.view !== 'playing') return say(statusLine(state));
  if (alreadyGuessed(state.guesses, word)) return say(`You already guessed ${word}. ${statusLine(state)}`);
  if (state.busy) return say('One moment, the last guess is still saving.');
  const outcome = await deps.game.guessWord(word, guessRequestId(deps.scope(), state.puzzleDate, word));
  if (outcome.status === 'busy') return say('One moment, the last guess is still saving.');
  if (outcome.status === 'error') return say(state.notice || friendlyError(outcome.code));
  return say(`${describeGuess(outcome.guess)} ${statusLine(state)}`);
}

async function remainingCommand(deps: WordleVoiceDeps, say: Say): Promise<string> {
  await deps.game.ensureLoaded();
  const state = deps.game.state;
  const unavailable = unavailableText(state);
  if (unavailable) return say(unavailable);
  if (state.view === 'won' || state.view === 'lost') return say(statusLine(state));
  const left = guessesLeft(state);
  return say(`${left} ${left === 1 ? 'guess' : 'guesses'} left.`);
}

async function guessesCommand(deps: WordleVoiceDeps, say: Say): Promise<string> {
  await deps.game.ensureLoaded();
  const state = deps.game.state;
  const unavailable = unavailableText(state);
  if (unavailable) return say(unavailable);
  if (!state.guesses.length) return say("You haven't guessed yet today.");
  return say(`You've guessed ${state.guesses.map((guess) => guess.word).join(', ')}. ${statusLine(state)}`);
}

async function confirmRevealCommand(deps: WordleVoiceDeps, say: Say): Promise<string> {
  // Refresh first so a terminal result reached on another device is honoured.
  await deps.game.load();
  const state = deps.game.state;
  const answer = releasedAnswer(state);
  if (answer) return say(`The answer was ${answer}.`);
  const unavailable = unavailableText(state);
  if (unavailable) return say(unavailable);
  return say("I can't reveal today's answer while the game is in progress.");
}

/**
 * Finalized-transcript Wordle adapter (#110).
 *
 * The adapter owns no game state and calls no conversational generation: every
 * command is deterministic, idempotent by segment id, and speaks from the
 * server-owned state shared with the stage.
 */
export function createWordleVoice(deps: WordleVoiceDeps) {
  const seen = new Set<string>();
  let pendingReveal = false;

  const say: Say = (text) => {
    deps.speak(text);
    return text;
  };

  function remember(key: string) {
    seen.add(key);
    if (seen.size > MAX_SEEN) {
      const oldest = seen.values().next().value;
      if (oldest !== undefined) seen.delete(oldest);
    }
  }

  function askReveal(): string {
    pendingReveal = true;
    return say('Revealing the answer ends the surprise. Say yes to reveal it, or no to keep playing.');
  }

  async function execute(command: WordleCommand): Promise<VoiceResult> {
    switch (command.kind) {
      case 'play':
        return { handled: true, command: command.kind, spoken: await playCommand(deps, say) };
      case 'guess':
        return { handled: true, command: command.kind, spoken: await guessCommand(deps, say, command.word) };
      case 'guess_invalid':
        return { handled: true, command: command.kind, spoken: say(command.spoken) };
      case 'remaining':
        return { handled: true, command: command.kind, spoken: await remainingCommand(deps, say) };
      case 'guesses':
        return { handled: true, command: command.kind, spoken: await guessesCommand(deps, say) };
      case 'quit':
        deps.game.close();
        return {
          handled: true,
          command: command.kind,
          spoken: say('Wordle closed. Your progress is saved, and the call is still on.'),
        };
      case 'reveal':
        return { handled: true, command: command.kind, spoken: askReveal() };
      case 'confirm':
        if (!pendingReveal) return { handled: false, ignored: 'nothing-pending' };
        pendingReveal = false;
        return { handled: true, command: command.kind, spoken: await confirmRevealCommand(deps, say) };
      case 'cancel':
        if (!pendingReveal) return { handled: false, ignored: 'nothing-pending' };
        pendingReveal = false;
        return { handled: true, command: command.kind, spoken: say("Okay, I won't reveal it.") };
      case 'ambiguous':
        // Ambiguity mutates nothing and never reaches the game service.
        return { handled: true, command: command.kind, spoken: say(command.spoken) };
    }
  }

  async function handleFinalized(input: FinalInput): Promise<VoiceResult> {
    const key = input.segmentId ? `${input.epoch ?? 0}:${input.segmentId}` : null;
    if (key && seen.has(key)) return { handled: false, ignored: 'duplicate' };
    if (key) remember(key);

    const command = parseWordleCommand(input.text);
    if (!command) {
      pendingReveal = false;
      return { handled: false, ignored: 'unrecognized' };
    }
    // Any new instruction supersedes a pending spoiler confirmation.
    if (pendingReveal && command.kind !== 'confirm' && command.kind !== 'cancel' && command.kind !== 'reveal') {
      pendingReveal = false;
    }
    return execute(command);
  }

  async function handleEvent(event: VoiceEvent): Promise<VoiceResult> {
    if (event.type !== 'transcript') return { handled: false, ignored: 'unrecognized' };
    if (event.speaker !== 'user') return { handled: false, ignored: 'assistant' };
    if (!event.final) return { handled: false, ignored: 'partial' };
    return handleFinalized({ text: event.text, segmentId: event.segmentId, epoch: event.epoch });
  }

  return { handleEvent, handleFinalized, pendingReveal: () => pendingReveal };
}

export type WordleVoice = ReturnType<typeof createWordleVoice>;
