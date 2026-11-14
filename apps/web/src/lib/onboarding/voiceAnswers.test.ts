import { describe, expect, it } from 'vitest';
import type { VoiceOption } from '../api';
import { interpretVoiceAnswer } from './voiceAnswers';
import type { OnboardingProgress, OnboardingStage } from './onboarding';

const VOICES: VoiceOption[] = [
  { id: 'voice_narrator', name: 'British Narrator', languages: ['en'] },
  { id: 'voice_warm', name: 'Warm Friend', languages: ['en'] },
];

function progress(stage: OnboardingStage): OnboardingProgress {
  return {
    schema_version: 1,
    revision: 1,
    stage,
    preferred_name: null,
    selected_voice_id: null,
    selected_pace: null,
    intro_played_at: null,
    started_at: '2026-11-14T09:00:00+00:00',
    completed_at: null,
    suspended_from: null,
    last_event_id: null,
    greeting: null,
    voice_application: null,
    capabilities: { speaking_pace: true, weather: true, memory: true, wordle: true, chat: true },
  };
}

const say = (text: string, final = true) => ({ text, final, speaker: 'user' as const, id: 'turn-1' });

describe('finalized voice answers', () => {
  it('never advances on a partial or an assistant utterance', () => {
    expect(interpretVoiceAnswer(progress('preferred_name'), say('Sam', false), VOICES)).toEqual({ type: 'ignore' });
    expect(interpretVoiceAnswer(progress('preferred_name'), { ...say('Sam'), speaker: 'assistant' }, VOICES)).toEqual({
      type: 'ignore',
    });
  });

  it('extracts and confirms a name only when it is not a question', () => {
    expect(interpretVoiceAnswer(progress('greeting'), say('My name is Sam'), VOICES)).toEqual({
      type: 'submit_name',
      name: 'Sam',
    });
    expect(interpretVoiceAnswer(progress('preferred_name'), say('Call me Ada Lovelace.'), VOICES)).toEqual({
      type: 'submit_name',
      name: 'Ada Lovelace',
    });
    expect(interpretVoiceAnswer(progress('preferred_name'), say("What's the weather?"), VOICES)).toEqual({
      type: 'suspend',
    });
  });

  it.each(['I want weather', "play today's Wordle", 'tell me a joke', 'what can you do'])(
    'never captures an unrelated request as a name: %s',
    (text) => {
      expect(interpretVoiceAnswer(progress('preferred_name'), say(text), VOICES)).toEqual({ type: 'suspend' });
    },
  );

  it('accepts a real name with an explicit prefix but rejects state phrases', () => {
    expect(interpretVoiceAnswer(progress('preferred_name'), say('My name is Sam'), VOICES)).toEqual({
      type: 'submit_name',
      name: 'Sam',
    });
    expect(interpretVoiceAnswer(progress('preferred_name'), say("I'm Ada"), VOICES)).toEqual({
      type: 'submit_name',
      name: 'Ada',
    });
    expect(interpretVoiceAnswer(progress('preferred_name'), say("I'm bored"), VOICES)).toEqual({ type: 'suspend' });
  });

  it('accepts a short bare name but not a bare capability word', () => {
    expect(interpretVoiceAnswer(progress('preferred_name'), say('Sam'), VOICES)).toEqual({
      type: 'submit_name',
      name: 'Sam',
    });
    expect(interpretVoiceAnswer(progress('preferred_name'), say('memory'), VOICES)).toEqual({ type: 'suspend' });
  });

  it('chooses a voice by catalog name or id, and keeps the current one on request', () => {
    expect(interpretVoiceAnswer(progress('voice_choice'), say('British Narrator'), VOICES)).toEqual({
      type: 'choose_voice',
      voiceId: 'voice_narrator',
    });
    expect(interpretVoiceAnswer(progress('voice_choice'), say('voice_warm'), VOICES)).toEqual({
      type: 'choose_voice',
      voiceId: 'voice_warm',
    });
    expect(interpretVoiceAnswer(progress('voice_choice'), say('keep this voice'), VOICES)).toEqual({
      type: 'choose_voice',
      voiceId: null,
    });
    expect(interpretVoiceAnswer(progress('voice_choice'), say('what voices are there?'), VOICES)).toEqual({
      type: 'suspend',
    });
  });

  it('maps pace words and suspends on an unrelated request', () => {
    expect(interpretVoiceAnswer(progress('speaking_pace'), say('a bit slower please'), VOICES)).toEqual({
      type: 'choose_pace',
      pace: 'slower',
    });
    expect(interpretVoiceAnswer(progress('speaking_pace'), say('faster'), VOICES)).toEqual({
      type: 'choose_pace',
      pace: 'faster',
    });
    expect(interpretVoiceAnswer(progress('speaking_pace'), say('normal'), VOICES)).toEqual({
      type: 'choose_pace',
      pace: 'normal',
    });
    expect(interpretVoiceAnswer(progress('speaking_pace'), say('play wordle'), VOICES)).toEqual({ type: 'suspend' });
  });

  it('acknowledges capabilities, skips globally and resumes from suspension', () => {
    expect(interpretVoiceAnswer(progress('capability_intro'), say('ready'), VOICES)).toEqual({ type: 'finish' });
    expect(interpretVoiceAnswer(progress('greeting'), say('skip setup'), VOICES)).toEqual({ type: 'skip' });
    expect(interpretVoiceAnswer(progress('suspended'), say('continue setup'), VOICES)).toEqual({ type: 'resume' });
    expect(interpretVoiceAnswer(progress('suspended'), say('what is the weather'), VOICES)).toEqual({ type: 'ignore' });
    expect(interpretVoiceAnswer(progress('awaiting_voice_ack'), say('continue'), VOICES)).toEqual({
      type: 'acknowledge_voice',
    });
  });

  it('never advances a completed or skipped instance', () => {
    expect(interpretVoiceAnswer(progress('completed'), say('Sam'), VOICES)).toEqual({ type: 'ignore' });
    expect(interpretVoiceAnswer(progress('skipped'), say('Sam'), VOICES)).toEqual({ type: 'ignore' });
  });
});
