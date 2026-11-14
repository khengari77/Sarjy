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
    preference = nextThemePreference(preference);
    storeTheme(browserStorage(), preference);
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
    start,
  };
}
