import { describe, expect, it } from 'vitest';
import { mergeVoices } from './session';

const voice = (id: string, name: string) => ({ id, name, languages: [] });

describe('mergeVoices', () => {
  it('appends a new page without dropping earlier voices', () => {
    expect(mergeVoices([voice('a', 'A')], [voice('b', 'B')])).toEqual([voice('a', 'A'), voice('b', 'B')]);
  });

  it('lets the later page replace a duplicate id in place', () => {
    expect(mergeVoices([voice('a', 'Old')], [voice('a', 'New')])).toEqual([voice('a', 'New')]);
  });
});
