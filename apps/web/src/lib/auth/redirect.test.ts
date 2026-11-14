import { describe, expect, it } from 'vitest';
import { safeNextPath } from './redirect';

describe('safeNextPath', () => {
  it.each([
    [null, '/'],
    ['', '/'],
    ['/conversations', '/conversations'],
    ['/conversations?voice=1', '/conversations?voice=1'],
    ['https://evil.example', '/'],
    ['//evil.example', '/'],
    ['/\\evil.example', '/'],
    ['javascript:alert(1)', '/'],
    ['/ok\\https://evil.example', '/'],
    ['/logout', '/'],
    ['/chat?token=abc&voice=1', '/chat?voice=1'],
  ])('resolves %s to %s', (input, expected) => {
    expect(safeNextPath(input)).toBe(expected);
  });
});
