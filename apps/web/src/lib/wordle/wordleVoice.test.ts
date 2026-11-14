import { describe, expect, it } from 'vitest';
import { releasedAnswer } from './wordleVoice';
import { setup, transcript, ZERO_MARKS } from './wordleVoiceSupport';

describe('Wordle voice adapter: transcript gating', () => {
  it('ignores partial transcripts entirely', async () => {
    const { fake, voice } = setup();
    const result = await voice.handleEvent(transcript('guess crane', 'partial-1', { final: false }));
    expect(result).toEqual({ handled: false, ignored: 'partial' });
    expect(fake.spoken).toEqual([]);
    expect(fake.calls).toMatchObject({ open: 0, load: 0, guesses: [] });
  });

  it('ignores assistant transcripts', async () => {
    const { fake, voice } = setup();
    expect(await voice.handleEvent(transcript('guess crane', 'a1', { speaker: 'assistant' }))).toEqual({
      handled: false,
      ignored: 'assistant',
    });
    expect(fake.calls.guesses).toEqual([]);
  });

  it('does not submit a partial even when it already spells a valid word', async () => {
    const { fake, voice } = setup();
    await voice.handleEvent(transcript('guess crane', 'p1', { final: false }));
    await voice.handleEvent(transcript('guess crane', 'p2', { final: false }));
    expect(fake.calls.guesses).toEqual([]);
  });

  it('leaves ordinary conversation untouched', async () => {
    const { fake, voice } = setup();
    expect(await voice.handleEvent(transcript('what is the weather in Paris', 's1'))).toEqual({
      handled: false,
      ignored: 'unrecognized',
    });
    expect(fake.spoken).toEqual([]);
  });
});

describe('Wordle voice adapter: commands', () => {
  it('opens the stage and loads exactly once for a duplicated play segment', async () => {
    const { fake, voice } = setup();
    const first = await voice.handleEvent(transcript('Play Wordle', 's1'));
    const second = await voice.handleEvent(transcript('Play Wordle', 's1'));
    expect(first.handled).toBe(true);
    expect(second).toEqual({ handled: false, ignored: 'duplicate' });
    expect(fake.calls.open).toBe(1);
    expect(fake.calls.ensureLoaded).toBe(1);
    expect(fake.spoken[0]).toContain("Today's Wordle is ready");
  });

  it('submits exactly one guess with a deterministic request id', async () => {
    const { fake, voice } = setup({ attemptsRemaining: 5 });
    const result = await voice.handleEvent(transcript('Guess C R A N E', 's1'));
    expect(result.command).toBe('guess');
    expect(fake.calls.guesses).toEqual([{ word: 'CRANE', id: 'scope1-2026-11-14-CRANE' }]);
    expect(fake.spoken[0]).toContain('CRANE');
    expect(fake.spoken[0]).toContain('5 guesses left.');
  });

  it('re-prompts a malformed guess without calling the server', async () => {
    const { fake, voice } = setup();
    const result = await voice.handleEvent(transcript('guess crane please extra', 's1'));
    expect(result.command).toBe('guess_invalid');
    expect(fake.calls.guesses).toEqual([]);
    expect(fake.spoken[0]).toContain('five-letter word');
  });

  it('rejects a duplicate guess locally without consuming an attempt', async () => {
    const { fake, voice } = setup({
      guesses: [{ word: 'CRANE', marks: ZERO_MARKS }],
      attemptsRemaining: 5,
    });
    await voice.handleEvent(transcript('guess crane', 's1'));
    expect(fake.calls.guesses).toEqual([]);
    expect(fake.spoken[0]).toContain('already guessed CRANE');
  });

  it('does not duplicate a successful guess when the same command is retried', async () => {
    const { fake, voice } = setup();
    await voice.handleEvent(transcript('guess crane', 's1'));
    await voice.handleEvent(transcript('guess crane', 's1'));
    expect(fake.calls.guesses).toHaveLength(1);
    await voice.handleEvent(transcript('guess crane', 's2'));
    expect(fake.calls.guesses).toHaveLength(1);
    expect(fake.spoken.at(-1)).toContain('already guessed CRANE');
  });

  it('reads remaining attempts and the current guesses from the server state', async () => {
    const { fake, voice } = setup({
      guesses: [
        { word: 'CRANE', marks: ZERO_MARKS },
        { word: 'SLATE', marks: ZERO_MARKS },
      ],
      attemptsRemaining: 4,
    });
    await voice.handleEvent(transcript('how many guesses left', 's1'));
    await voice.handleEvent(transcript('what did I guess', 's2'));
    expect(fake.spoken[0]).toBe('4 guesses left.');
    expect(fake.spoken[1]).toContain('CRANE, SLATE');
    expect(fake.calls.guesses).toEqual([]);
  });

  it('asks for clarification and mutates nothing for ambiguous speech', async () => {
    const { fake, voice } = setup();
    const result = await voice.handleEvent(transcript('play the wordle with my friends', 's1'));
    expect(result.command).toBe('ambiguous');
    expect(fake.calls).toMatchObject({ open: 0, load: 0, guesses: [] });
    expect(fake.spoken[0]).toContain('What would you like');
  });

  it('quits by closing the stage only', async () => {
    const { fake, voice } = setup({ open: true });
    const result = await voice.handleEvent(transcript('Quit Wordle', 's1'));
    expect(result.command).toBe('quit');
    expect(fake.calls.close).toBe(1);
    expect(fake.state.open).toBe(false);
    expect(fake.spoken[0]).toContain('call is still on');
  });
});

describe('Wordle voice adapter: failure paths', () => {
  it('reuses the request id after a recoverable failure', async () => {
    let attempt = 0;
    const { fake, voice } = setup({}, (word) => {
      attempt += 1;
      return attempt === 1
        ? { status: 'error', code: 'WORDLE_RATE_LIMITED' }
        : { status: 'ok', guess: { word, marks: ZERO_MARKS } };
    });
    await voice.handleEvent(transcript('guess crane', 's1'));
    expect(fake.spoken[0]).toContain('Too many guesses');
    await voice.handleEvent(transcript('guess crane', 's2'));
    expect(fake.calls.guesses.map((guess) => guess.id)).toEqual(['scope1-2026-11-14-CRANE', 'scope1-2026-11-14-CRANE']);
    expect(fake.state.guesses).toHaveLength(1);
  });

  it('surfaces an unavailable puzzle as recoverable speech without a guess', async () => {
    const { fake, voice } = setup({ view: 'unavailable', notice: "Today's Wordle isn't available right now." });
    await voice.handleEvent(transcript('guess crane', 's1'));
    expect(fake.calls.guesses).toEqual([]);
    expect(fake.spoken[0]).toContain("isn't available");
  });

  it('surfaces a server error without inventing a result', async () => {
    const { fake, voice } = setup({}, () => ({ status: 'error', code: 'WORDLE_GUESS_INVALID' }));
    await voice.handleEvent(transcript('guess crane', 's1'));
    expect(fake.spoken[0]).toContain('five-letter word');
    expect(fake.state.guesses).toEqual([]);
  });

  it('reports a busy guess instead of racing a second submission', async () => {
    const { fake, voice } = setup();
    fake.state.busy = true;
    await voice.handleEvent(transcript('guess crane', 's1'));
    expect(fake.calls.guesses).toEqual([]);
    expect(fake.spoken[0]).toContain('still saving');
  });
});

describe('Wordle voice adapter: spoilers', () => {
  it('asks before revealing and never leaks the answer pre-terminal', async () => {
    const { fake, voice } = setup({ view: 'playing', answer: 'PLANT' });
    await voice.handleEvent(transcript('reveal the answer', 's1'));
    expect(fake.spoken[0]).toContain('Say yes');
    expect(fake.spoken.join(' ')).not.toContain('PLANT');
    expect(voice.pendingReveal()).toBe(true);
    await voice.handleEvent(transcript('yes', 's2'));
    expect(fake.calls.load).toBe(1);
    expect(fake.spoken.join(' ')).not.toContain('PLANT');
    expect(fake.spoken.at(-1)).toContain("can't reveal");
    expect(releasedAnswer(fake.state)).toBeUndefined();
  });

  it('cancels a spoiler confirmation', async () => {
    const { fake, voice } = setup({ view: 'playing', answer: 'PLANT' });
    await voice.handleEvent(transcript('reveal the answer', 's1'));
    await voice.handleEvent(transcript('no', 's2'));
    expect(voice.pendingReveal()).toBe(false);
    expect(fake.calls.load).toBe(0);
    expect(fake.spoken.at(-1)).toContain("won't reveal");
    expect(fake.spoken.join(' ')).not.toContain('PLANT');
  });

  it('returns the server answer after confirmation once the game is terminal', async () => {
    const { fake, voice } = setup({ view: 'lost', answer: 'PLANT' });
    await voice.handleEvent(transcript('reveal the answer', 's1'));
    expect(fake.spoken.at(-1)).not.toContain('PLANT');
    await voice.handleEvent(transcript('yes', 's2'));
    expect(fake.spoken.at(-1)).toBe('The answer was PLANT.');
  });

  it('does not treat a bare yes as a command without a pending reveal', async () => {
    const { fake, voice } = setup();
    const result = await voice.handleEvent(transcript('yes', 's1'));
    expect(result).toEqual({ handled: false, ignored: 'nothing-pending' });
    expect(fake.calls.load).toBe(0);
    expect(fake.spoken).toEqual([]);
  });
});
