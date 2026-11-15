/**
 * Theme preference resolution for the Living Cloud shell.
 *
 * A preference is the user's explicit (or system-following) choice and is
 * persisted per browser in localStorage. The resolved theme is the concrete
 * `data-theme` value stamped on <html>. All colour work is cosmetic: nothing
 * here touches the voice session, canvas or orb.
 *
 * SSR must never read storage. Callers only invoke the storage helpers after
 * mount; the inline boot script in app.html performs the pre-paint pass.
 */

export const THEME_STORAGE_KEY = 'sarjy-theme';
export const THEME_ATTRIBUTE = 'data-theme';
export const THEME_PREFERENCE_ATTRIBUTE = 'data-theme-preference';
export const THEME_PREFERENCES = ['system', 'light', 'dark'] as const;

export type ThemePreference = (typeof THEME_PREFERENCES)[number];
export type ResolvedTheme = 'light' | 'dark';

export interface ThemeAttributes {
  theme: ResolvedTheme;
  preference: ThemePreference;
}

/** Narrow arbitrary storage content to a supported preference. */
export function parseThemePreference(value: string | null | undefined): ThemePreference {
  return THEME_PREFERENCES.includes(value as ThemePreference) ? (value as ThemePreference) : 'system';
}

/** Resolve a preference against the current OS colour scheme. */
export function resolveTheme(preference: ThemePreference, systemPrefersDark: boolean): ResolvedTheme {
  if (preference === 'system') return systemPrefersDark ? 'dark' : 'light';
  return preference;
}

/** Next preference in the header control's cycle: system → light → dark. */
export function nextThemePreference(preference: ThemePreference): ThemePreference {
  if (preference === 'system') return 'light';
  if (preference === 'light') return 'dark';
  return 'system';
}

/** Human-readable mode name used by the header control's accessible name. */
export function themeName(preference: ThemePreference): string {
  if (preference === 'light') return 'Light';
  if (preference === 'dark') return 'Dark';
  return 'System';
}

export function themeAttributes(preference: ThemePreference, systemPrefersDark: boolean): ThemeAttributes {
  return { theme: resolveTheme(preference, systemPrefersDark), preference };
}

/**
 * Read the saved preference. Accepts a `null` storage (SSR, blocked storage)
 * and degrades to the system preference without throwing.
 */
export function readStoredTheme(storage: Pick<Storage, 'getItem'> | null | undefined): ThemePreference {
  try {
    return parseThemePreference(storage?.getItem(THEME_STORAGE_KEY));
  } catch {
    return 'system';
  }
}

/** Persist the preference; blocked or full storage stays non-fatal. */
export function storeTheme(storage: Pick<Storage, 'setItem'> | null | undefined, preference: ThemePreference): void {
  try {
    storage?.setItem(THEME_STORAGE_KEY, preference);
  } catch {
    // Presentation-only preference: quota/private-mode failures must not break the app.
  }
}

/** Stamp the resolved theme and raw preference onto the supplied root element. */
export function applyTheme(
  root: Pick<HTMLElement, 'setAttribute'>,
  preference: ThemePreference,
  systemPrefersDark: boolean,
): ThemeAttributes {
  const attributes = themeAttributes(preference, systemPrefersDark);
  root.setAttribute(THEME_ATTRIBUTE, attributes.theme);
  root.setAttribute(THEME_PREFERENCE_ATTRIBUTE, preference);
  return attributes;
}
