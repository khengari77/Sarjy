<script lang="ts">
  import { getContext, tick } from 'svelte';
  import type { Turn } from '../api';
  import type { OrbPresentation } from '../orb/orbState';
  import VoiceOrb from '../orb/VoiceOrb.svelte';
  import CaptionBubble from './CaptionBubble.svelte';
  import type { Words } from './words';
  import type { ProvisionalTranscript } from '../voice/connection';
  import type { PendingIntent } from './intent';

  let {
    presentation,
    micLevel,
    outputLevel,
    arabic,
    live,
    connecting,
    connectionLost,
    voiceErrorCode,
    voiceErrorText,
    audioBlocked,
    pendingIntent = null,
    turns,
    provisional = null,
    captionsVisible,
    busy,
    words,
    returning = false,
    onretrymic,
    onreconnect,
    onend,
    onenableaudio,
    onorb,
    ontranscript,
    oncancelintent,
    onconfirmintent,
  }: {
    presentation: OrbPresentation;
    micLevel: number;
    outputLevel: number;
    arabic: boolean;
    live: boolean;
    connecting: boolean;
    connectionLost: boolean;
    voiceErrorCode: string;
    voiceErrorText: string;
    audioBlocked: boolean;
    pendingIntent?: PendingIntent;
    turns: Turn[];
    provisional?: ProvisionalTranscript | null;
    captionsVisible: boolean;
    busy: boolean;
    words: Words;
    returning?: boolean;
    onretrymic: () => void;
    onreconnect: () => void;
    onend: () => void;
    onenableaudio: () => void;
    onorb: () => void;
    /** Opens the right drawer on its Transcript tab; `opener` receives focus back. */
    ontranscript: (opener: HTMLElement) => void;
    oncancelintent: () => void;
    onconfirmintent: () => void;
  } = $props();

  const idleHero = $derived(!live && !connecting && !turns.length && !provisional);
  // The transcript stays reachable whenever there is, or could soon be, text.
  const showBubble = $derived(live || connecting || turns.length > 0 || provisional !== null);
  const hasNotice = $derived(Boolean(voiceErrorText) || (connectionLost && live) || audioBlocked);
  const statusText = $derived(arabic ? presentation.statusLabel.ar : presentation.statusLabel.en);
  const healthyPhase = $derived(
    !connectionLost &&
      (presentation.effectivePhase === 'listening' ||
        presentation.effectivePhase === 'thinking' ||
        presentation.effectivePhase === 'speaking'),
  );
  const stageStatusText = $derived(
    connectionLost
      ? words.offline
      : live && presentation.effectivePhase === 'connecting'
        ? words.reconnecting
        : statusText,
  );

  // The shell owns the modal layer; this dialog is moved into its region so it
  // is never inside the inert stage (see AppShell's `stageDialogOpen`).
  const dialogRegion = getContext<(() => HTMLElement | null) | undefined>('shell-dialog-region');

  function portal(node: HTMLElement) {
    const target = dialogRegion?.();
    if (!target) return;
    target.appendChild(node);
    return {
      destroy() {
        node.remove();
      },
    };
  }

  // The only Escape handler for the dialog: the shell yields Escape while
  // `stageDialogOpen`, and a window listener fires wherever focus is. Keeping
  // it off the dialog element avoids one keypress cancelling twice.
  function onDialogEscape(event: KeyboardEvent) {
    if (event.key === 'Escape') oncancelintent();
  }

  let switchCancel: HTMLButtonElement | undefined = $state();

  $effect(() => {
    if (!pendingIntent) return;
    // A drawer may have been open when this dialog was requested; the shell
    // hands off modal ownership asynchronously, so focus once that settles.
    void tick().then(() => switchCancel?.focus());
  });
</script>

<svelte:window onkeydown={pendingIntent ? onDialogEscape : undefined} />

<section class="voice-stage" data-live={live} dir={arabic ? 'rtl' : 'ltr'}>
  <svg class="stage-waves" viewBox="0 0 1440 900" preserveAspectRatio="none" aria-hidden="true" focusable="false">
    <path class="wave wave-primary" d="M-40 560 C 220 470 420 650 720 610 S 1180 470 1480 545" />
    <path class="wave wave-accent" d="M-40 615 C 240 530 460 735 760 670 S 1200 525 1480 600" />
  </svg>

  {#if pendingIntent}
    <div class="confirm-scrim" role="presentation" use:portal>
      <div class="confirm-dialog" role="dialog" aria-modal="true" tabindex="-1" aria-labelledby="switch-title">
        <h2 id="switch-title">{arabic ? 'إنهاء المكالمة الحالية؟' : 'End the current voice session?'}</h2>
        <p>
          {pendingIntent.type === 'create'
            ? arabic
              ? 'بدء محادثة جديدة ينهي المكالمة الجارية. يبقى السجل محفوظًا.'
              : 'Starting a new conversation ends the live call. The transcript stays saved.'
            : arabic
              ? 'تبديل المحادثة ينهي المكالمة الجارية. يبقى السجل محفوظًا.'
              : 'Switching conversations ends the live call. The transcript stays saved.'}
        </p>
        <div class="confirm-actions">
          <button class="ghost-button" bind:this={switchCancel} onclick={oncancelintent}>
            {arabic ? 'إلغاء' : 'Cancel'}
          </button>
          <button class="primary-button" onclick={onconfirmintent}>
            {pendingIntent.type === 'create'
              ? arabic
                ? 'إنهاء وبدء'
                : 'End & start'
              : arabic
                ? 'إنهاء وتبديل'
                : 'End & switch'}
          </button>
        </div>
      </div>
    </div>
  {/if}

  {#if live}
    <h1 class="stage-heading">{words.conversationTitle}</h1>
  {/if}

  <div class="orb-zone">
    <div class="orb-wrap" style:--orb-glow-color={presentation.primaryColor}>
      <span class="orb-glow" aria-hidden="true"></span>
      <VoiceOrb
        {presentation}
        {micLevel}
        {outputLevel}
        label={arabic ? presentation.ariaLabel.ar : presentation.ariaLabel.en}
        statusLabel={arabic ? presentation.statusLabel.ar : presentation.statusLabel.en}
        disabled={busy}
        onactivate={onorb}
      />
    </div>

    <!-- Everything under the orb lives in one fixed-height block, so a long
         reply, a notice or a state change can never move the orb or the
         composer. -->
    <div class="below" data-hero={idleHero}>
      {#if idleHero}
        <h1 class="hero-title">{returning ? words.welcomeBack : words.title}</h1>
        {#if hasNotice}
          {@render notices()}
        {:else}
          <p class="intro">{words.intro}</p>
        {/if}
        <p class="status-pill idle-pill status-line" data-phase={presentation.effectivePhase}>
          <span class="status-dot" aria-hidden="true"></span>
          {statusText}
        </p>
      {:else}
        {#if live}
          <p class="status-line live-phase" data-phase={presentation.effectivePhase}>{stageStatusText}</p>
          {#if healthyPhase}
            <span class="status-pill connected-pill">
              <span class="status-dot" aria-hidden="true"></span>
              {words.connected}
            </span>
          {/if}
        {:else if connecting}
          <p class="status-line" data-phase={presentation.effectivePhase}>
            <span class="status-dot" aria-hidden="true"></span>
            {statusText}
          </p>
        {:else}
          <p class="status-pill idle-pill status-line" data-phase={presentation.effectivePhase}>
            <span class="status-dot" aria-hidden="true"></span>
            {statusText}
          </p>
        {/if}
        <div class="caption-slot">
          {#if hasNotice}
            {@render notices()}
          {:else if showBubble}
            <CaptionBubble {turns} {provisional} {arabic} {captionsVisible} onopen={ontranscript} />
          {/if}
        </div>
      {/if}
    </div>
  </div>
</section>

{#snippet notices()}
  <div class="stage-notices">
    {#if audioBlocked}
      <div class="stage-notice">
        <span>{words.audioBlocked}</span>
        <button class="primary-button" onclick={onenableaudio}>
          {arabic ? 'تشغيل الصوت' : 'Enable audio'}
        </button>
      </div>
    {/if}
    {#if voiceErrorText}
      <div class="stage-notice" role="status" data-code={voiceErrorCode}>
        <span>{voiceErrorText}</span>
        {#if voiceErrorCode.startsWith('MICROPHONE_')}
          <button class="ghost-button" disabled={busy} onclick={onretrymic}>
            {arabic ? 'إعادة محاولة الميكروفون' : 'Retry microphone'}
          </button>
        {/if}
      </div>
    {/if}
    {#if connectionLost && live}
      <div class="stage-notice" role="status" data-code="CONNECTION_LOST">
        <span>{arabic ? 'انقطع الاتصال.' : 'Connection lost.'}</span>
        <button class="ghost-button" disabled={busy} onclick={onreconnect}>
          {arabic ? 'إعادة الاتصال' : 'Reconnect'}
        </button>
        <button class="ghost-button" disabled={busy} onclick={onend}>
          {arabic ? 'إنهاء' : 'End'}
        </button>
      </div>
    {/if}
  </div>
{/snippet}

<style>
  /* Idle and connecting are not healthy live states: a green dot would claim a
     connection that is not there. The live phases keep chat.css's green. */
  .status-line[data-phase='idle'] .status-dot,
  .status-line[data-phase='connecting'] .status-dot,
  .status-pill[data-phase='idle'] .status-dot,
  .status-pill[data-phase='connecting'] .status-dot {
    background: var(--text-muted);
    box-shadow: none;
  }
</style>
