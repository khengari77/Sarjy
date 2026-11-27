<script lang="ts">
  import { onMount } from 'svelte';
  import { GENERIC_ACCESS_ERROR } from './messages';
  import './entry.css';

  interface Props {
    error?: boolean;
    blocked?: boolean;
    next?: string;
  }

  let { error = false, blocked = false, next = '/' }: Props = $props();
  let form = $state<HTMLFormElement | null>(null);
  let input = $state<HTMLInputElement | null>(null);
  let showToken = $state(false);
  const message = $derived(blocked ? 'Too many attempts. Try again shortly.' : GENERIC_ACCESS_ERROR);

  // Fragments never reach the server, so `#token=` can only be exchanged
  // client-side. Strip it from history before posting and let the plain form
  // action do the rest; with JavaScript disabled the field is still usable.
  onMount(() => {
    const match = /^#token=(.+)$/.exec(window.location.hash);
    if (!match || !form || !input) return;
    let token = match[1];
    try {
      token = decodeURIComponent(token);
    } catch {
      return;
    }
    history.replaceState(history.state, '', `${window.location.pathname}${window.location.search}`);
    input.value = token;
    form.requestSubmit();
  });
</script>

<svelte:head>
  <title>Sarjy — Access</title>
  <meta name="robots" content="noindex" />
</svelte:head>

<main class="entry-shell">
  <div class="entry-orb" aria-hidden="true"></div>
  <section class="entry-panel">
    <h1 class="visually-hidden">Sarjy access</h1>
    <form bind:this={form} method="POST" class="entry-form">
      <input type="hidden" name="next" value={next} />
      <label for="access-token">Access token</label>
      <div class="password-field">
        <input
          bind:this={input}
          id="access-token"
          name="token"
          type={showToken ? 'text' : 'password'}
          autocomplete="off"
          autocapitalize="none"
          spellcheck="false"
          required
          aria-invalid={error ? 'true' : undefined}
          aria-describedby={error ? 'access-error' : undefined}
        />
        <button
          type="button"
          class="password-toggle"
          aria-pressed={showToken}
          aria-label={showToken ? 'Hide access token' : 'Show access token'}
          onclick={() => (showToken = !showToken)}
        >
          {showToken ? 'Hide' : 'Show'}
        </button>
      </div>
      <p id="access-error" class="entry-error" role="alert">{error ? message : ''}</p>
      <button type="submit">Enter</button>
    </form>
  </section>
</main>
