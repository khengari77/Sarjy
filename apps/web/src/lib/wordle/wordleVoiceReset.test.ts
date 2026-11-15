import { describe, expect, it } from 'vitest';
import { setup, transcript, ZERO_MARKS } from './wordleVoiceSupport';

describe('Wordle voice adapter: reset', () => {
  it('asks first, resets only on yes and speaks the fresh game', async () => {
    const { fake, voice } = setup({ view: 'playing', guesses: [{ word: 'CRANE', marks: ZERO_MARKS }] });
    const asked = await voice.handleEvent(transcript('reset wordle', 's1'));
    expect(asked.command).toBe('reset');
    expect(fake.spoken[0]).toBe("Reset today's game? Say yes to confirm.");
    expect(fake.calls.reset).toBe(0);
    expect(voice.pending()).toBe('reset');
    await voice.handleEvent(transcript('yes', 's2'));
    expect(fake.calls.reset).toBe(1);
    expect(fake.spoken.at(-1)).toBe('Game reset. Six guesses left.');
    expect(fake.pendingFlags).toEqual(['reset', null]);
  });

  it('declines on no and cancels on anything else', async () => {
    const { fake, voice } = setup({ view: 'playing' });
    await voice.handleEvent(transcript('start over', 's1'));
    await voice.handleEvent(transcript('no', 's2'));
    expect(fake.calls.reset).toBe(0);
    expect(fake.spoken.at(-1)).toBe("Okay, I won't reset it.");

    await voice.handleEvent(transcript('reset the game', 's3'));
    const other = await voice.handleEvent(transcript("what's the weather", 's4'));
    expect(other).toMatchObject({ handled: false, ignored: 'unrecognized' });
    expect(voice.pending()).toBe(null);
    await voice.handleEvent(transcript('yes', 's5'));
    expect(fake.calls.reset).toBe(0);
  });

  it('arms the confirmation when the model tool asked, without speaking itself', async () => {
    const { fake, voice } = setup({ view: 'playing' });
    voice.expectReset();
    expect(fake.spoken).toEqual([]);
    await voice.handleEvent(transcript('yes', 's1'));
    expect(fake.calls.reset).toBe(1);
  });

  it('keeps the generic phrases for an open game only', async () => {
    const { fake, voice } = setup({ open: false });
    expect(await voice.handleEvent(transcript('start over', 's1'))).toMatchObject({ ignored: 'unrecognized' });
    expect(fake.spoken).toEqual([]);
  });
});
