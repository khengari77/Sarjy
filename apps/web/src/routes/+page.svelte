<script lang="ts">
  import { onMount } from 'svelte';
  import type { VoiceRuntime } from '../lib/audio/voice-runtime';
  import {
    friendlyVoiceError,
    isFatalVoiceError,
    phaseFromAgentState,
    phaseFromTransportStatus,
  } from '../lib/audio/livekitAdapter';
  import { deriveOrbPresentation, INITIAL_STATE, type VoicePhase } from '../lib/orb/orbState';
  import type { ResolvedTheme } from '../lib/ui/theme';
  import AppShell from '../lib/ui/AppShell.svelte';
  import ConversationList from '../lib/chat/ConversationList.svelte';
  import VoiceStage from '../lib/chat/VoiceStage.svelte';
  import type { PendingIntent } from '../lib/chat/intent';
  import CallControls from '../lib/chat/CallControls.svelte';
  import Composer from '../lib/chat/Composer.svelte';
  import VoiceSettings from '../lib/chat/VoiceSettings.svelte';
  import RightDrawer from '../lib/ui/RightDrawer.svelte';
  import { createAnnouncement } from '../lib/chat/announcement.svelte';
  import { wordsFor } from '../lib/chat/words';
  import { initialVoiceState, type VoiceConnection } from '../lib/voice/connection';
  import { errorCode } from '../lib/voice/errors';
  import { createCatalogStore, initialCatalogState } from '../lib/voice/catalog';
  import { initialConversationState, type ConversationStore } from '../lib/voice/conversations';
  import { bootStudio } from '../lib/voice/studio';
  import { createSelectionController } from '../lib/voice/selection';
  import '../lib/chat/chat.css';

  // English-first release: Arabic copy and provider plumbing stay in place but
  // the reviewer-facing interface is fixed to English.
  const LANGUAGE_SWITCH_ENABLED = false;
  let arabic = $state(false);
  let catalog = $state(initialCatalogState());
  let conversation = $state(initialConversationState());
  let draft = $state('');
  let pendingIntent = $state<PendingIntent>(null);
  let devOverlay = $state<'none' | 'memory_work' | 'confirming' | 'muted' | 'error'>('none');
  let busy = $state(false);
  let captionsVisible = $state(true);
  // The shell owns storage and OS-preference resolution and reports the answer
  // through onthemechange. `dark` matches the tokens.css SSR fallback, so the
  // server and the first client frame agree before the boot script resolves.
  let theme = $state<ResolvedTheme>('dark');
  let voice = $state(initialVoiceState());
  let runtime: VoiceRuntime | undefined;
  let connection: VoiceConnection | undefined;
  let conversations: ConversationStore | undefined;
  let catalogStore: ReturnType<typeof createCatalogStore> | undefined;
  const selection = createSelectionController({
    voice,
    getPending: () => pendingIntent,
    setPending: (intent) => (pendingIntent = intent),
    run: (work) => run(work),
    cancelConnect: () => connection!.cancelConnect(),
    endVoice: () => connection!.end(),
    getConversations: () => conversations!,
  });
  const words = $derived(wordsFor(arabic));

  const phase = $derived<VoicePhase>(
    (voice.agentState ? phaseFromAgentState(voice.agentState) : null) ??
      phaseFromTransportStatus(voice.transportStatus) ??
      'idle',
  );
  const canInterrupt = $derived(phase === 'thinking' || phase === 'speaking');
  const fatalError = $derived(voice.voiceErrorCode !== '' && isFatalVoiceError(voice.voiceErrorCode));
  const voiceErrorText = $derived(voice.voiceErrorCode ? friendlyVoiceError(voice.voiceErrorCode, arabic) : '');
  const presentation = $derived(
    deriveOrbPresentation(
      {
        ...INITIAL_STATE,
        phase,
        overlay:
          devOverlay !== 'none' ? devOverlay : fatalError ? 'error' : voice.live && voice.muted ? 'muted' : 'none',
        micActive: voice.live !== null && !voice.muted,
        sessionId: voice.live?.id ?? null,
        connectionReady: voice.live !== null && !voice.connectionLost,
        micLevel: voice.micLevel,
        outputLevel: voice.outputLevel,
      },
      theme,
    ),
  );

  async function run(work: () => Promise<void>, onError?: (error: unknown) => void) {
    busy = true;
    try {
      await work();
    } catch (error) {
      if (onError) onError(error);
      else conversation.listError = errorCode(error);
    } finally {
      busy = false;
    }
  }

  function onVoiceError(error: unknown) {
    voice.voiceErrorCode = errorCode(error);
  }

  function handleOrb() {
    if (voice.live) {
      if (voice.muted) void toggleMute();
      else if (canInterrupt) void interrupt();
      return;
    }
    if (!voice.connecting) startConnect();
  }

  function startConnect() {
    void connection!.connect(true).catch(onVoiceError);
  }

  async function toggleMute() {
    await run(() => connection!.toggleMute(), onVoiceError);
  }

  async function interrupt() {
    if (!voice.live || !canInterrupt) return;
    await run(() => connection!.interrupt(), onVoiceError);
  }

  async function send() {
    if (!draft.trim()) return;
    await run(async () => {
      if (!voice.live) await connection!.connect(false);
      if (!voice.live) return;
      await connection!.sendText(draft.trim());
      draft = '';
    }, onVoiceError);
  }

  async function enableAudio() {
    await run(async () => {
      await runtime?.resumeAudio?.();
      voice.audioBlocked = false;
    }, onVoiceError);
  }

  function toggleCaptions() {
    captionsVisible = !captionsVisible;
    localStorage.setItem('sarjy-captions', captionsVisible ? 'on' : 'off');
  }

  function toggleLanguage() {
    arabic = !arabic;
    localStorage.setItem('sarjy-language', arabic ? 'ar' : 'en');
  }

  const announcement = createAnnouncement(() => (arabic ? presentation.statusLabel.ar : presentation.statusLabel.en));

  onMount(() => {
    const boot = bootStudio({
      voice,
      catalog,
      conversation,
      restoreLanguage: LANGUAGE_SWITCH_ENABLED,
      setArabic: (value) => (arabic = value),
      setCaptions: (value) => (captionsVisible = value),
      setDevOverlay: (value) => (devOverlay = value),
      getLanguage: () => (arabic ? 'ar' : 'en'),
      run: (work, onError) => run(work, onError),
      onVoiceError,
    });
    runtime = boot.runtime;
    connection = boot.connection;
    conversations = boot.conversations;
    catalogStore = boot.catalogStore;
    return boot.cleanup;
  });
</script>

<svelte:head>
  <title>Sarjy — Voice Context Studio</title>
  <meta name="description" content="An English-first voice assistant with transparent context." />
</svelte:head>

<AppShell
  dir={arabic ? 'rtl' : 'ltr'}
  stageDialogOpen={pendingIntent !== null}
  languageLabel={arabic ? 'English' : 'العربية'}
  onLanguage={LANGUAGE_SWITCH_ENABLED ? toggleLanguage : undefined}
  leftLabel={arabic ? 'المحادثات' : 'Conversations'}
  rightLabel={arabic ? 'الذاكرة والإعدادات' : 'Memory and settings'}
  onthemechange={(value) => (theme = value)}
>
  {#snippet left()}
    <ConversationList
      conversations={conversation.conversations}
      current={conversation.current}
      {arabic}
      {busy}
      loading={conversation.loading}
      error={conversation.listError}
      onselect={selection.requestSelect}
      oncreate={selection.create}
      onretry={() => run(() => conversations!.reload())}
    />
  {/snippet}

  {#snippet stage()}
    <VoiceStage
      {presentation}
      micLevel={voice.micLevel}
      outputLevel={voice.outputLevel}
      {arabic}
      live={voice.live !== null}
      connecting={voice.connecting}
      connectionLost={voice.connectionLost}
      voiceErrorCode={voice.voiceErrorCode}
      {voiceErrorText}
      audioBlocked={voice.audioBlocked}
      {pendingIntent}
      turns={conversation.turns}
      provisional={voice.provisional}
      tools={conversation.toolCalls}
      {captionsVisible}
      {busy}
      {words}
      onretrymic={startConnect}
      onreconnect={() => run(() => connection!.reconnect(), onVoiceError)}
      onend={() => run(() => connection!.end(), onVoiceError)}
      onenableaudio={enableAudio}
      onorb={handleOrb}
      oncancelintent={() => (pendingIntent = null)}
      onconfirmintent={() => selection.confirmSwitch()}
    />
  {/snippet}

  <!-- Reachable while an overlay drawer is modal: #37 requires Mute and End to
       stay available, and the composer is useful beside them. -->
  {#snippet controls()}
    <CallControls
      live={voice.live}
      connecting={voice.connecting}
      {busy}
      muted={voice.muted}
      connectionLost={voice.connectionLost}
      {canInterrupt}
      {words}
      onend={() => run(() => connection!.end(), onVoiceError)}
      oncancel={() => run(() => connection!.cancelConnect(), onVoiceError)}
      ontoggle={toggleMute}
      oninterrupt={interrupt}
      onconnect={startConnect}
    />
    <Composer bind:draft placeholder={words.placeholder} sendLabel={words.send} disabled={busy} onsend={send} />
    <small class="attribution"><a href="https://open-meteo.com/">Weather data: Open-Meteo</a></small>
  {/snippet}

  {#snippet right()}
    <RightDrawer {arabic}>
      {#snippet voicePanel()}
        <VoiceSettings
          {arabic}
          voices={catalog.voices}
          bind:voiceId={catalog.voiceId}
          bind:voiceSearch={catalog.search}
          nextVoicePage={catalog.nextPage}
          voiceLoading={catalog.loading}
          voiceError={catalog.error}
          live={voice.live}
          activeVoiceName={voice.activeVoiceName}
          {captionsVisible}
          onsearch={() => void catalogStore!.load()}
          onloadmore={() => void catalogStore!.load(true)}
          onremember={() => catalogStore!.remember()}
          ontoggleCaptions={toggleCaptions}
        />
      {/snippet}
    </RightDrawer>
  {/snippet}
</AppShell>

<p class="sr-only" aria-live="polite" aria-atomic="true">{announcement.message}</p>
