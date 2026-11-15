<script lang="ts">
  import { themeName } from './theme';
  import type { createThemeSession } from './themeSession.svelte';

  let { theme }: { theme: ReturnType<typeof createThemeSession> } = $props();

  /**
   * The header shows light/dark as a segmented control. Selecting the mode
   * that is already the explicit preference returns to `system`, which keeps
   * the OS-following option reachable without a third pill.
   */
  function choose(mode: 'light' | 'dark') {
    theme.set(theme.preference === mode ? 'system' : mode);
  }
</script>

<div class="theme-control" role="group" aria-label="Theme">
  <button
    type="button"
    class="theme-button"
    data-active={theme.resolved === 'light'}
    aria-pressed={theme.preference === 'light'}
    aria-label="Theme: Light"
    title={`${themeName('light')} theme${theme.preference === 'light' ? ' — activate to follow your system' : ''}`}
    onclick={() => choose('light')}
  >
    <svg aria-hidden="true" viewBox="0 0 24 24" width="18" height="18" fill="none">
      <circle cx="12" cy="12" r="4.1" stroke="currentColor" stroke-width="1.6" />
      <path
        d="M12 2.6v2.1M12 19.3v2.1M2.6 12h2.1M19.3 12h2.1M5.3 5.3l1.5 1.5M17.2 17.2l1.5 1.5M18.7 5.3l-1.5 1.5M6.8 17.2l-1.5 1.5"
        stroke="currentColor"
        stroke-width="1.6"
        stroke-linecap="round"
      />
    </svg>
  </button>
  <button
    type="button"
    class="theme-button"
    data-active={theme.resolved === 'dark'}
    aria-pressed={theme.preference === 'dark'}
    aria-label="Theme: Dark"
    title={`${themeName('dark')} theme${theme.preference === 'dark' ? ' — activate to follow your system' : ''}`}
    onclick={() => choose('dark')}
  >
    <svg aria-hidden="true" viewBox="0 0 24 24" width="18" height="18" fill="none">
      <path
        d="M20.1 14.4A8.3 8.3 0 0 1 9.6 3.9a8.3 8.3 0 1 0 10.5 10.5Z"
        stroke="currentColor"
        stroke-width="1.6"
        stroke-linejoin="round"
      />
    </svg>
  </button>
</div>
