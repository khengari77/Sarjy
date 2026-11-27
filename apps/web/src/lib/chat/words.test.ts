import { describe, expect, it } from 'vitest';
import { friendlyVoiceError } from '../audio/livekitAdapter';
import { wordsFor } from './words';

describe('audio playback copy has one source of truth', () => {
  it.each([false, true])('uses the friendly error copy (arabic=%s)', (arabic) => {
    expect(wordsFor(arabic).audioBlocked).toBe(friendlyVoiceError('AUDIO_PLAYBACK_BLOCKED', arabic));
  });

  it('provides Arabic reconnecting and offline wording', () => {
    const words = wordsFor(true);
    expect(words.reconnecting).toBe('جارٍ إعادة الاتصال…');
    expect(words.offline).toBe('غير متصل');
  });
});
