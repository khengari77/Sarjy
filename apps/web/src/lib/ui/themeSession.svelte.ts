import {
  applyTheme,
  nextThemePreference,
  readStoredTheme,
  resolveTheme,
  storeTheme,
  type ResolvedTheme,
  type ThemePreference,
} from './theme';
import { browserStorage } from '../storage';

/**
 * Theme session: the preference/storage/OS-resolution half of the shell.
 *
 * The shell is the only place that decides which theme is active. Consumers
 * such as the orb subscribe to `resolved` instead of re-deriving it from
 * `data-theme` or `prefers-color-scheme`. Resolution is cosmetic and never
 * touches the voice session, canvas or orb identity.
 */
export function createThemeSession(onResolved?: (theme: ResolvedTheme) => void) {
  let preference = $state<ThemePreference>('system');
  let systemDark = $state(false);
  const resolved = $derived(resolveTheme(preference, systemDark));

  // Report only after the browser resolves storage; SSR keeps the CSS fallback.
  $effect(() => {
    onResolved?.(resolved);
  });

  function cycle() {
    set(nextThemePreference(preference));
  }

  /**
   * Apply one explicit preference (the header cycle target or a voice command).
   * Persistence and OS resolution stay here so callers never touch storage or
   * `data-theme` directly. Out-of-range values cannot occur: the command
   * contract only carries the three supported preferences.
   */
  function set(next: ThemePreference) {
    preference = next;
    storeTheme(browserStorage(), next);
    applyTheme(document.documentElement, preference, systemDark);
  }

  /** Adopt the saved preference and follow later OS changes. */
  function start() {
    const system = window.matchMedia('(prefers-color-scheme: dark)');
    preference = readStoredTheme(browserStorage());
    systemDark = system.matches;
    applyTheme(document.documentElement, preference, systemDark);

    const onSchemeChange = (event: MediaQueryListEvent) => {
      systemDark = event.matches;
      if (preference === 'system') applyTheme(document.documentElement, preference, systemDark);
    };
    system.addEventListener('change', onSchemeChange);
    return () => system.removeEventListener('change', onSchemeChange);
  }

  return {
    get resolved() {
      return resolved;
    },
    get preference() {
      return preference;
    },
    cycle,
    set,
    start,
  };
}

export type ThemeSession = ReturnType<typeof createThemeSession>;

/** The one imperative surface a voice theme command is allowed to use. */
export type ThemeControl = Pick<ThemeSession, 'set'>;
