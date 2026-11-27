<script lang="ts">
  import { enhance } from '$app/forms';
  import { resolve } from '$app/paths';
  import type { AccountAction } from './account';
  import { OAUTH_PROVIDER_LABELS, type OAuthProvider } from './oauth';
  import './entry.css';
  import './account.css';

  interface Props {
    action: AccountAction;
    next: string;
    email: string;
    message: string;
    providers: OAuthProvider[];
  }

  let { action, next, email, message, providers }: Props = $props();
  const signUp = $derived(action === 'sign-up');
  const title = $derived(signUp ? 'Create your account' : 'Sign in');
  const pendingLabel = $derived(signUp ? 'Creating account...' : 'Signing in...');
  const query = $derived(`next=${encodeURIComponent(next)}`);
  let submitting = $state(false);
  let showPassword = $state(false);
</script>

<svelte:head>
  <title>Sarjy — {title}</title>
  <meta name="robots" content="noindex" />
</svelte:head>

<main class="entry-shell">
  <div class="entry-orb" aria-hidden="true"></div>
  <section class="entry-panel">
    <h1 class="account-title">{title}</h1>
    <form
      method="POST"
      class="entry-form"
      use:enhance={() => {
        submitting = true;
        return async ({ update }) => {
          await update();
          submitting = false;
        };
      }}
    >
      <input type="hidden" name="next" value={next} />
      <label for="account-email">Email</label>
      <input
        id="account-email"
        name="email"
        type="email"
        dir="ltr"
        autocomplete={signUp ? 'email' : 'username'}
        autocapitalize="none"
        spellcheck="false"
        maxlength="254"
        required
        value={email}
        aria-invalid={message ? 'true' : undefined}
        aria-describedby={message ? 'account-error' : undefined}
      />
      <label for="account-password">Password</label>
      <div class="password-field">
        <input
          id="account-password"
          name="password"
          type={showPassword ? 'text' : 'password'}
          dir="ltr"
          autocomplete={signUp ? 'new-password' : 'current-password'}
          minlength={signUp ? 8 : undefined}
          required
          aria-invalid={message ? 'true' : undefined}
          aria-describedby={message ? 'account-error' : signUp ? 'account-hint' : undefined}
        />
        <button
          type="button"
          class="password-toggle"
          aria-pressed={showPassword}
          aria-label={showPassword ? 'Hide password' : 'Show password'}
          onclick={() => (showPassword = !showPassword)}
        >
          {showPassword ? 'Hide' : 'Show'}
        </button>
      </div>
      {#if signUp}<p id="account-hint" class="account-hint">At least 8 characters.</p>{/if}
      <p id="account-error" class="entry-error" role="alert">{message}</p>
      <button type="submit" disabled={submitting}>{submitting ? pendingLabel : title}</button>
    </form>
    {#if providers.length > 0}
      <div class="account-providers">
        <p class="account-divider">or</p>
        <ul class="account-provider-list">
          {#each providers as provider (provider)}
            <li>
              <a class="account-provider" href={resolve(`/auth/[provider]?${query}`, { provider })}>
                Continue with {OAUTH_PROVIDER_LABELS[provider]}
              </a>
            </li>
          {/each}
        </ul>
      </div>
    {/if}
    <p class="account-switch">
      {#if signUp}
        Already have an account? <a href={resolve(`/sign-in?${query}`)}>Sign in</a>
      {:else}
        New to Sarjy? <a href={resolve(`/sign-up?${query}`)}>Create an account</a>
      {/if}
    </p>
  </section>
</main>
