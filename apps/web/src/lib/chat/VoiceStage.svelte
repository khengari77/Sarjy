<script lang="ts">
  import { getContext, tick } from 'svelte';
  import type { Turn } from '../api';
  import type { OrbPresentation } from '../orb/orbState';
  import VoiceOrb from '../orb/VoiceOrb.svelte';
  import Transcript from './Transcript.svelte';
  import TranscriptDrawer from './TranscriptDrawer.svelte';
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
    tools,
    captionsVisible,
    busy,
    words,
    onretrymic,
    onreconnect,
    onend,
    onenableaudio,
    onorb,
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
    tools: { id: string; name: string; result: unknown }[];
    captionsVisible: boolean;
    busy: boolean;
    words: Words;
    onretrymic: () => void;
    onreconnect: () => void;
    onend: () => void;
    onenableaudio: () => void;
    onorb: () => void;
    oncancelintent: () => void;
    onconfirmintent: () => void;
  } = $props();

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

  let transcriptOpen = $state(false);
  let transcriptButton: HTMLButtonElement | undefined = $state();
  let switchCancel: HTMLButtonElement | undefined = $state();

  $effect(() => {
    if (!pendingIntent) return;
    // A drawer may have been open when this dialog was requested; the shell
    // hands off modal ownership asynchronously, so focus once that settles.
    void tick().then(() => switchCancel?.focus());
  });

  async function openTranscript() {
    transcriptOpen = true;
  }

  async function closeTranscript() {
    transcriptOpen = false;
    await tick();
    transcriptButton?.focus();
  }
</script>

<svelte:window onkeydown={pendingIntent ? onDialogEscape : undefined} />

<section class="voice-stage" dir={arabic ? 'rtl' : 'ltr'}>
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

  <p class="status-line" data-phase={presentation.effectivePhase} role="status">
    <span class="status-dot" style:background-color={presentation.primaryColor} aria-hidden="true"></span>
    {arabic ? presentation.statusLabel.ar : presentation.statusLabel.en}
  </p>

  {#if voiceErrorText}
    <div class="alert" role="alert" data-code={voiceErrorCode}>
      <p>{voiceErrorText}</p>
      {#if voiceErrorCode.startsWith('MICROPHONE_')}
        <button class="ghost-button" disabled={busy} onclick={onretrymic}>
          {arabic ? 'إعادة محاولة الميكروفون' : 'Retry microphone'}
        </button>
      {/if}
    </div>
  {/if}

  {#if connectionLost && live}
    <div class="alert" role="status" data-code="CONNECTION_LOST">
      <p>{arabic ? 'انقطع الاتصال.' : 'Connection lost.'}</p>
      <button class="ghost-button" disabled={busy} onclick={onreconnect}>
        {arabic ? 'إعادة الاتصال' : 'Reconnect'}
      </button>
      <button class="ghost-button" disabled={busy} onclick={onend}>
        {arabic ? 'إنهاء' : 'End'}
      </button>
    </div>
  {/if}

  {#if audioBlocked}
    <button class="primary-button" onclick={onenableaudio}>
      {arabic ? 'تشغيل الصوت' : 'Enable audio'}
    </button>
  {/if}

  <div class="orb-zone">
    <VoiceOrb
      {presentation}
      {micLevel}
      {outputLevel}
      label={arabic ? presentation.ariaLabel.ar : presentation.ariaLabel.en}
      statusLabel={arabic ? presentation.statusLabel.ar : presentation.statusLabel.en}
      disabled={busy}
      onactivate={onorb}
    />
    {#if !live && !connecting && !turns.length && !provisional}
      <h1>{words.title}</h1>
      <p class="intro">{words.intro}</p>
    {/if}
  </div>

  <Transcript
    {turns}
    {provisional}
    {tools}
    {arabic}
    {captionsVisible}
    expanded={transcriptOpen}
    onexpand={openTranscript}
  />

  {#if live || turns.length || provisional}
    <button class="ghost-button transcript-trigger" bind:this={transcriptButton} type="button" onclick={openTranscript}>
      {arabic ? 'سجل المحادثة' : 'Transcript'}
    </button>
  {/if}
</section>

<TranscriptDrawer open={transcriptOpen} {turns} {provisional} {tools} {arabic} onclose={closeTranscript} />
