import type { Rgb } from './types';

const HEX = /^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i;

/** Strict parser: only three- or six-digit hex literals are accepted. */
export function parseHexColor(value: string): Rgb | null {
  if (!HEX.test(value)) return null;
  const digits = value.slice(1);
  const expanded =
    digits.length === 3
      ? digits
          .split('')
          .map((digit) => digit + digit)
          .join('')
      : digits;
  const parsed = Number.parseInt(expanded, 16);
  if (!Number.isFinite(parsed)) return null;
  return [((parsed >> 16) & 255) / 255, ((parsed >> 8) & 255) / 255, (parsed & 255) / 255];
}

/** Safely convert a literal colour, falling back instead of parsing a prefix. */
export function hexToRgb(value: string, fallback: Rgb = [0, 0, 0]): Rgb {
  return parseHexColor(value) ?? fallback;
}

function channel(value: number): number {
  return Math.min(1, Math.max(0, value));
}

function toHex(component: number): string {
  return Math.round(channel(component) * 255)
    .toString(16)
    .padStart(2, '0');
}

/** Blend two literal colours and return a literal hex colour. */
export function mixHexColors(base: string, other: string, weight: number): string {
  const first = parseHexColor(base);
  const second = parseHexColor(other);
  if (!first || !second) throw new Error('UNSUPPORTED_COLOR');
  const ratio = Math.min(1, Math.max(0, weight));
  const mixed = first.map((component, index) => component * ratio + second[index] * (1 - ratio));
  return '#' + mixed.map(toHex).join('');
}
