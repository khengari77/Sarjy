import { describe, expect, it } from 'vitest';
import { clampLevel, levelForPhase, type LevelPhase } from './levels';

const PHASES: readonly LevelPhase[] = [
  'idle',
  'connecting',
  'listening',
  'thinking',
  'speaking',
  'disconnected',
  'error',
];

describe('level mapping', () => {
  it('clamps mic and output samples into 0..1', () => {
    expect(clampLevel(-0.4)).toBe(0);
    expect(clampLevel(0)).toBe(0);
    expect(clampLevel(0.42)).toBe(0.42);
    expect(clampLevel(1)).toBe(1);
    expect(clampLevel(3.7)).toBe(1);
    expect(clampLevel(Number.NaN)).toBe(0);
    expect(clampLevel(Number.POSITIVE_INFINITY)).toBe(0);
  });

  it('drives listening and connecting from the microphone level', () => {
    expect(levelForPhase('listening', 0.63, 0.9)).toBe(0.63);
    expect(levelForPhase('connecting', 0.24, 0.9)).toBe(0.24);
  });

  it('drives speaking from the output level', () => {
    expect(levelForPhase('speaking', 0.9, 0.31)).toBe(0.31);
  });

  it('keeps idle, thinking, disconnected and error silent', () => {
    for (const phase of ['idle', 'thinking', 'disconnected', 'error'] as const) {
      expect(levelForPhase(phase, 0.8, 0.8), phase).toBe(0);
    }
  });

  it('clamps whatever the phase chose', () => {
    for (const phase of PHASES) {
      const level = levelForPhase(phase, 4, -2);
      expect(level).toBeGreaterThanOrEqual(0);
      expect(level).toBeLessThanOrEqual(1);
    }
  });
});
