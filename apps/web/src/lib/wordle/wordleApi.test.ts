import { describe, expect, it, vi } from 'vitest';
import { createWordleClient, normalizeGuessResult, normalizeStats, normalizeToday, type RequestFn } from './wordleApi';

describe('normalizeToday', () => {
  it('reads date, available flag and server guesses', () => {
    const today = normalizeToday({
      date: '2026-11-13',
      available: true,
      game: {
        status: 'in_progress',
        guesses: [{ word: 'crane', marks: ['absent', 'present', 'absent', 'absent', 'correct'] }],
      },
    });
    expect(today.date).toBe('2026-11-13');
    expect(today.available).toBe(true);
    expect(today.game?.guesses[0]).toEqual({
      word: 'CRANE',
      marks: ['absent', 'present', 'absent', 'absent', 'correct'],
    });
  });

  it('reports unavailable without inventing a game', () => {
    expect(normalizeToday({ date: '2026-11-13', available: false })).toEqual({
      date: '2026-11-13',
      available: false,
      game: null,
      answer: undefined,
    });
  });

  it('drops an answer the server should not send before terminal state', () => {
    const today = normalizeToday({
      date: '2026-11-13',
      available: true,
      game: { status: 'in_progress', guesses: [] },
      answer: 'PLANT',
    });
    expect(today.answer).toBeUndefined();
  });

  it('accepts the answer at terminal state', () => {
    const today = normalizeToday({
      date: '2026-11-13',
      available: true,
      game: { status: 'lost', guesses: [], answer: 'plant' },
    });
    expect(today.answer).toBe('PLANT');
  });

  it('reads the terminal answer and attempts remaining from the game payload', () => {
    const today = normalizeToday({
      date: '2026-11-13',
      available: true,
      game: {
        id: 'game-1',
        status: 'won',
        attempts_remaining: 3,
        guesses: [{ word: 'crane', marks: ['green', 'green', 'green', 'green', 'green'] }],
        answer: 'crane',
      },
    });
    expect(today).toMatchObject({ date: '2026-11-13', available: true, answer: 'CRANE' });
    expect(today.game).toMatchObject({ id: 'game-1', status: 'won', attemptsRemaining: 3 });
  });

  it('keeps an unavailable code without inventing a board', () => {
    expect(normalizeToday({ date: '2026-11-13', available: false, game: null, code: 'WORDLE_DISABLED' })).toEqual({
      date: '2026-11-13',
      available: false,
      game: null,
      code: 'WORDLE_DISABLED',
      timezone: undefined,
    });
  });

  it('accepts green/yellow/gray aliases and numeric marks', () => {
    const aliased = normalizeGuessResult({ marks: ['green', 'yellow', 'gray', 'grey', 'absent'], status: 'won' });
    expect(aliased.marks).toEqual(['correct', 'present', 'absent', 'absent', 'absent']);
    const numeric = normalizeGuessResult({ marks: [2, 1, 0, 0, 2], status: 'won' });
    expect(numeric.marks).toEqual(['correct', 'present', 'absent', 'absent', 'correct']);
    const objects = normalizeGuessResult({ marks: [2, 1, 0, 1, 2].map((mark) => ({ mark })), status: 'won' });
    expect(objects.marks).toEqual(['correct', 'present', 'absent', 'present', 'correct']);
  });

  it.each([
    [null],
    [{ date: 'today', available: true }],
    [{ date: '2026-11-13' }],
    [{ date: '2026-11-13', available: true }],
    [{ date: '2026-11-13', available: true, game: { status: 'in_progress', guesses: [{ word: 'TOOLONG' }] } }],
  ])('rejects malformed payloads (%j)', (payload) => {
    expect(() => normalizeToday(payload)).toThrow('WORDLE_RESPONSE_INVALID');
  });
});

describe('normalizeGuessResult', () => {
  it('keeps the answer only when the guess ends the game', () => {
    expect(
      normalizeGuessResult({
        marks: ['absent', 'absent', 'absent', 'absent', 'absent'],
        status: 'in_progress',
        answer: 'PLANT',
      }).answer,
    ).toBeUndefined();
    expect(
      normalizeGuessResult({
        marks: ['absent', 'absent', 'absent', 'absent', 'absent'],
        status: 'lost',
        answer: 'PLANT',
      }).answer,
    ).toBe('PLANT');
  });

  it('reads the echoed game, including the nested terminal answer', () => {
    const result = normalizeGuessResult({
      id: 'game-1',
      date: '2026-11-13',
      status: 'lost',
      attempts_remaining: 0,
      guesses: [{ word: 'ALLEY', marks: ['gray', 'yellow', 'gray', 'gray', 'gray'] }],
      answer: 'PLANT',
      marks: ['gray', 'yellow', 'gray', 'gray', 'gray'],
    });
    expect(result.marks).toEqual(['absent', 'present', 'absent', 'absent', 'absent']);
    expect(result.status).toBe('lost');
    expect(result.answer).toBe('PLANT');
    expect(result.game).toMatchObject({
      id: 'game-1',
      attemptsRemaining: 0,
      guesses: [{ word: 'ALLEY', marks: ['absent', 'present', 'absent', 'absent', 'absent'] }],
    });
  });

  it('drops a nested answer while the game is still in progress', () => {
    const result = normalizeGuessResult({
      status: 'in_progress',
      guesses: [{ word: 'ALLEY', marks: ['gray', 'gray', 'gray', 'gray', 'gray'] }],
      answer: 'PLANT',
      marks: ['gray', 'gray', 'gray', 'gray', 'gray'],
    });
    expect(result.answer).toBeUndefined();
  });
});

describe('normalizeStats', () => {
  it('requires four non-negative integers', () => {
    expect(normalizeStats({ played: 4, won: 3, current_streak: 1, max_streak: 2 })).toEqual({
      played: 4,
      won: 3,
      current_streak: 1,
      max_streak: 2,
    });
    expect(() => normalizeStats({ played: 4, won: 3, current_streak: 1 })).toThrow('WORDLE_RESPONSE_INVALID');
    expect(() => normalizeStats({ played: -1, won: 0, current_streak: 0, max_streak: 0 })).toThrow(
      'WORDLE_RESPONSE_INVALID',
    );
    expect(() => normalizeStats({ played: 1.5, won: 0, current_streak: 0, max_streak: 0 })).toThrow(
      'WORDLE_RESPONSE_INVALID',
    );
  });
});

describe('createWordleClient', () => {
  it('calls the contract endpoints and sends the idempotency key', async () => {
    const send = vi.fn<RequestFn>(async (path) => {
      if (path === 'wordle/today')
        return { date: '2026-11-13', available: true, game: { guesses: [], status: 'in_progress' } };
      if (path === 'wordle/guesses')
        return { marks: ['correct', 'correct', 'correct', 'correct', 'correct'], status: 'won', answer: 'PLANT' };
      return { played: 1, won: 1, current_streak: 1, max_streak: 1 };
    });
    const client = createWordleClient(send);
    await expect(client.today()).resolves.toMatchObject({ available: true });
    await expect(client.guess('PLANT', 'req-1')).resolves.toMatchObject({ status: 'won', answer: 'PLANT' });
    await expect(client.stats()).resolves.toMatchObject({ played: 1 });
    expect(send).toHaveBeenNthCalledWith(1, 'wordle/today');
    expect(send).toHaveBeenNthCalledWith(2, 'wordle/guesses', { guess: 'PLANT', request_id: 'req-1' });
    expect(send).toHaveBeenNthCalledWith(3, 'wordle/stats');
  });
});
