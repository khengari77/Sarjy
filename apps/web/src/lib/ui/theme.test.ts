import { expect, it } from 'vitest';
import {
  applyTheme,
  nextThemePreference,
  parseThemePreference,
  readStoredTheme,
  resolveTheme,
  storeTheme,
  THEME_STORAGE_KEY,
} from './theme';

it.each([
  ['light', 'light'],
  ['dark', 'dark'],
  ['system', 'system'],
  [null, 'system'],
  [undefined, 'system'],
  ['', 'system'],
  ['Light', 'system'],
  ['sepia', 'system'],
])('parses stored theme %s as %s', (value, expected) => {
  expect(parseThemePreference(value)).toBe(expected);
});

it.each([
  ['light', true, 'light'],
  ['light', false, 'light'],
  ['dark', true, 'dark'],
  ['dark', false, 'dark'],
  ['system', true, 'dark'],
  ['system', false, 'light'],
] as const)('resolves %s with system dark=%s to %s', (preference, systemDark, expected) => {
  expect(resolveTheme(preference, systemDark)).toBe(expected);
});

it('cycles system → light → dark → system', () => {
  expect(nextThemePreference('system')).toBe('light');
  expect(nextThemePreference('light')).toBe('dark');
  expect(nextThemePreference('dark')).toBe('system');
});

it('reads the saved preference without touching storage during SSR', () => {
  const storage = { getItem: (key: string) => (key === THEME_STORAGE_KEY ? 'light' : null) };
  expect(readStoredTheme(storage)).toBe('light');
  expect(readStoredTheme(null)).toBe('system');
  expect(readStoredTheme(undefined)).toBe('system');
});

it('treats unavailable storage as the system preference', () => {
  const storage = {
    getItem: () => {
      throw new Error('denied');
    },
  };
  expect(readStoredTheme(storage)).toBe('system');
});

it('persists the preference and swallows storage failures', () => {
  const writes: string[] = [];
  storeTheme({ setItem: (key: string, value: string) => writes.push(`${key}=${value}`) }, 'dark');
  expect(writes).toEqual([`${THEME_STORAGE_KEY}=dark`]);
  expect(() =>
    storeTheme(
      {
        setItem: () => {
          throw new Error('quota');
        },
      },
      'light',
    ),
  ).not.toThrow();
  expect(() => storeTheme(null, 'light')).not.toThrow();
});

it('stamps the resolved theme and raw preference on the root', () => {
  const attributes = new Map<string, string>();
  const root = { setAttribute: (name: string, value: string) => attributes.set(name, value) };
  expect(applyTheme(root, 'system', false)).toEqual({ theme: 'light', preference: 'system' });
  expect(attributes.get('data-theme')).toBe('light');
  expect(attributes.get('data-theme-preference')).toBe('system');
  applyTheme(root, 'dark', false);
  expect(attributes.get('data-theme')).toBe('dark');
  expect(attributes.get('data-theme-preference')).toBe('dark');
});
