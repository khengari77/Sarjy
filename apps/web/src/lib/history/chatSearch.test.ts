import { describe, expect, it } from 'vitest';
import { filterConversations, normalizeChatQuery } from './chatSearch';

const chat = (id: string, title: string) => ({ id, title, revision: 0 });

describe('normalizeChatQuery', () => {
  it('removes Arabic diacritics and tatweel', () => {
    expect(normalizeChatQuery('مُحَمَّد')).toBe('محمد');
    expect(normalizeChatQuery('محـــمد')).toBe('محمد');
  });

  it('folds hamza and letter variants', () => {
    expect(normalizeChatQuery('أحمد')).toBe('احمد');
    expect(normalizeChatQuery('إبراهيم')).toBe('ابراهيم');
    expect(normalizeChatQuery('مصطفى')).toBe('مصطفي');
    expect(normalizeChatQuery('ميزانية')).toBe('ميزانيه');
  });

  it('trims and lowercases Latin titles', () => {
    expect(normalizeChatQuery('  Trip  To PARIS ')).toBe('trip to paris');
  });
});

describe('filterConversations', () => {
  const conversations = [chat('c1', 'Trip to Paris'), chat('c2', 'ميزانية Tripoli'), chat('c3', 'أحمد في الرياض')];

  it('returns every conversation for an empty query', () => {
    expect(filterConversations(conversations, '   ')).toHaveLength(3);
  });

  it('matches Latin titles without a backend call', () => {
    expect(filterConversations(conversations, 'paris').map((entry) => entry.id)).toEqual(['c1']);
  });

  it('matches Arabic titles with diacritics and hamza variants', () => {
    expect(filterConversations(conversations, 'ميزانيه').map((entry) => entry.id)).toEqual(['c2']);
    expect(filterConversations(conversations, 'احمد').map((entry) => entry.id)).toEqual(['c3']);
  });

  it('returns no rows when nothing matches', () => {
    expect(filterConversations(conversations, 'zzz')).toEqual([]);
  });
});
