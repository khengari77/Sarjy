import { describe, expect, it } from 'vitest';
import { hexToRgb, mixHexColors, parseHexColor } from './colors';

describe('literal colour parsing', () => {
  it('parses three- and six-digit hex literals', () => {
    expect(parseHexColor('#4cc9b6')).toEqual([76 / 255, 201 / 255, 182 / 255]);
    expect(parseHexColor('#fff')).toEqual([1, 1, 1]);
  });

  it('rejects malformed, unsupported and CSS-expression inputs', () => {
    for (const value of ['', '#', '#12', '#12345', '4cc9b6', 'red', 'color-mix(in srgb, #4add91 55%, #5e7189)']) {
      expect(parseHexColor(value)).toBeNull();
    }
  });

  it('falls back instead of silently parsing a prefix', () => {
    expect(hexToRgb('color-mix(in srgb, #4add91 55%, #5e7189)')).toEqual([0, 0, 0]);
    expect(hexToRgb('#12345', [0.1, 0.2, 0.3])).toEqual([0.1, 0.2, 0.3]);
  });

  it('blends to a literal hex colour with the requested weight', () => {
    expect(mixHexColors('#4add91', '#5e7189', 0.55)).toBe('#53ac8d');
    expect(mixHexColors('#4add91', '#5e7189', 1)).toBe('#4add91');
    expect(mixHexColors('#4add91', '#5e7189', 0)).toBe('#5e7189');
  });

  it('rejects unsupported inputs to the mixer explicitly', () => {
    expect(() => mixHexColors('color-mix(in srgb, #4add91 55%, #5e7189)', '#5e7189', 0.55)).toThrow(
      'UNSUPPORTED_COLOR',
    );
  });
});
