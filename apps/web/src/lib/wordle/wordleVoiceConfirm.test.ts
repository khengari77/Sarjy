import { describe, expect, it } from 'vitest';
import { applyWordleToolEvent } from './wordleActive';
import { confirmGuessPrompt } from './wordleVoice';
import { setup, transcript } from './wordleVoiceSupport';

describe('Wordle voice adapter: spoken guesses are confirmed', () => {
  it('reads a spoken word back and records it only after yes', async () => {
    const { fake, voice } = setup();
    await voice.handleEvent(transcript('guess crane', 's1'));
    expect(fake.spoken.at(-1)).toBe('C, R, A, N, E. Is that your guess?');
    expect(fake.calls.guesses).toEqual([]);
    expect(fake.pendingFlags.at(-1)).toBe('guess');
    await voice.handleEvent(transcript('yes', 's2'));
    expect(fake.calls.guesses.map((guess) => guess.word)).toEqual(['CRANE']);
    expect(fake.pendingFlags.at(-1)).toBeNull();
  });

  it('drops the pending word on no and records nothing', async () => {
    const { fake, voice } = setup();
    await voice.handleEvent(transcript('guess crane', 's1'));
    await voice.handleEvent(transcript('no', 's2'));
    expect(fake.calls.guesses).toEqual([]);
    expect(fake.spoken.at(-1)).toBe('Okay, not that one. Say your guess again, or spell it.');
    expect(voice.pendingWord()).toBeNull();
  });

  it('commits spelled letters at once, replacing a word still awaiting a yes', async () => {
    const { fake, voice } = setup();
    await voice.handleEvent(transcript('guess train', 's1'));
    await voice.handleEvent(transcript('C R A N E', 's2'));
    expect(fake.calls.guesses.map((guess) => guess.word)).toEqual(['CRANE']);
    await voice.handleEvent(transcript('yes', 's3'));
    expect(fake.calls.guesses).toHaveLength(1);
  });

  it('a new spoken guess replaces the pending one', async () => {
    const { fake, voice } = setup();
    await voice.handleEvent(transcript('guess train', 's1'));
    await voice.handleEvent(transcript('guess crane', 's2'));
    expect(voice.pendingWord()).toBe('CRANE');
    await voice.handleEvent(transcript('yes', 's3'));
    expect(fake.calls.guesses.map((guess) => guess.word)).toEqual(['CRANE']);
  });

  it('refuses a word outside the word list without a confirmation turn', async () => {
    const { fake, voice } = setup();
    fake.allowed = () => false;
    await voice.handleEvent(transcript('guess crxne', 's1'));
    expect(fake.spoken.at(-1)).toBe("CRXNE isn't in the word list. Try another word.");
    expect(voice.pending()).toBeNull();
    expect(fake.calls.guesses).toEqual([]);
  });

  it('still asks when the word list cannot be checked', async () => {
    const { fake, voice } = setup();
    fake.allowed = () => null;
    await voice.handleEvent(transcript('guess crane', 's1'));
    expect(fake.spoken.at(-1)).toBe(confirmGuessPrompt('CRANE'));
  });

  it('keeps the pending guess through noise or an unrelated remark', async () => {
    const { fake, voice } = setup();
    await voice.handleEvent(transcript('guess crane', 's1'));
    await voice.handleEvent(transcript('hmm', 's2'));
    expect(voice.pendingWord()).toBe('CRANE');
    await voice.handleEvent(transcript('yes', 's3'));
    expect(fake.calls.guesses.map((guess) => guess.word)).toEqual(['CRANE']);
  });

  it('drops a pending guess once the game is closed, so a later yes is not a guess', async () => {
    const { fake, voice } = setup();
    await voice.handleEvent(transcript('guess crane', 's1'));
    fake.port.close();
    const result = await voice.handleEvent(transcript('yes', 's2'));
    expect(result).toEqual({ handled: false, ignored: 'nothing-pending' });
    expect(fake.calls.guesses).toEqual([]);
  });

  it('closing the popup clears a pending reset, so a later yes never resets the game', async () => {
    const { fake, voice } = setup();
    await voice.handleEvent(transcript('reset the game', 's1'));
    expect(voice.pending()).toBe('reset');
    voice.clearPending();
    expect(fake.pendingFlags.at(-1)).toBeNull();
    const result = await voice.handleEvent(transcript('yes', 's2'));
    expect(result).toEqual({ handled: false, ignored: 'nothing-pending' });
    expect(fake.calls.reset).toBe(0);
  });

  it('quitting by voice clears the pending guess', async () => {
    const { fake, voice } = setup();
    await voice.handleEvent(transcript('guess crane', 's1'));
    await voice.handleEvent(transcript('quit wordle', 's2'));
    expect(voice.pending()).toBeNull();
    expect(fake.calls.guesses).toEqual([]);
  });

  it('arms the yes/no for a word the worker read back', async () => {
    const { fake, voice } = setup();
    applyWordleToolEvent(fake.port, { type: 'wordle', action: 'confirm_guess', word: 'SLATE', epoch: 1 }, voice);
    expect(voice.pending()).toBe('guess');
    await voice.handleEvent(transcript('yes', 's1'));
    expect(fake.calls.guesses.map((guess) => guess.word)).toEqual(['SLATE']);
  });
});
