<script lang="ts">
  import { onMount } from 'svelte';
  import type { VoiceRuntime } from '../lib/audio/voice-runtime';
  import AppShell from '../lib/ui/AppShell.svelte';
  import ConversationList from '../lib/chat/ConversationList.svelte';
  import VoiceStage from '../lib/chat/VoiceStage.svelte';
  import type { PendingIntent } from '../lib/chat/intent';
  import CallControls from '../lib/chat/CallControls.svelte';
  import Composer from '../lib/chat/Composer.svelte';
  import VoiceSettings from '../lib/chat/VoiceSettings.svelte';
  import { voiceStatus } from '../lib/chat/voice-state';
  import { wordsFor } from '../lib/chat/words';
  import { initialVoiceState, type VoiceConnection } from '../lib/voice/connection';
  import { errorCode } from '../lib/voice/errors';
  import { createCatalogStore, initialCatalogState } from '../lib/voice/catalog';
  import { initialConversationState, type ConversationStore } from '../lib/voice/conversations';
  import { bootStudio } from '../lib/voice/studio';
  import { createSelectionController } from '../lib/voice/selection';
  import { deriveStage } from '../lib/voice/stageView';
  import '../lib/chat/chat.css';

  // English-first release: Arabic copy and provider plumbing stay in place but
  // the reviewer-facing interface is fixed to English.
  const LANGUAGE_SWITCH_ENABLED = false;
  let arabic = $state(false);
  let catalog = $state(initialCatalogState());
  let conversation = $state(initialConversationState());
  let draft = $state('');
  let pendingIntent = $state<PendingIntent>(null);
  let announcement = $state('');
  let devOverlay = $state<'none' | 'memory_work' | 'confirming' | 'muted' | 'error'>('none');
  let lastAnnounced = '';
  let busy = $state(false);
  let captionsVisible = $state(true);
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
  const selectedVoice = $derived(
    catalog.voices.find((entry) => entry.id === catalog.voiceId) ??
      (catalog.voiceId ? { id: catalog.voiceId, name: catalog.voiceId, languages: [] } : null),
  );
  // The server resolves the voice for the running call; a worker ACK is still
  // required before anything is labelled "active" (#112 owns that path).
  const sessionVoice = $derived(voice.live?.voice ?? null);
  const inputVoiceStatus = $derived(
    voiceStatus({ selected: selectedVoice, session: sessionVoice, confirmed: null, live: voice.live !== null }),
  );
  const view = $derived(deriveStage(voice, arabic, devOverlay));

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
      else if (view.canInterrupt) void interrupt();
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
    if (!voice.live || !view.canInterrupt) return;
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

  $effect(() => {
    const label = arabic ? view.presentation.statusLabel.ar : view.presentation.statusLabel.en;
    if (label === lastAnnounced) return;
    const timer = setTimeout(() => {
      lastAnnounced = label;
      announcement = label;
    }, 300);
    return () => clearTimeout(timer);
  });

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
  modeLabel={arabic ? 'بسيط' : 'Simple'}
  languageLabel={arabic ? 'English' : 'العربية'}
  onLanguage={LANGUAGE_SWITCH_ENABLED ? toggleLanguage : undefined}
  leftLabel={arabic ? 'المحادثات' : 'Conversations'}
  rightLabel={arabic ? 'الذاكرة والإعدادات' : 'Memory and settings'}
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
      presentation={view.presentation}
      micLevel={voice.micLevel}
      outputLevel={voice.outputLevel}
      {arabic}
      live={voice.live !== null}
      connecting={voice.connecting}
      connectionLost={voice.connectionLost}
      voiceErrorCode={voice.voiceErrorCode}
      voiceErrorText={view.errorText}
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
    <CallControls
      live={voice.live}
      connecting={voice.connecting}
      {busy}
      muted={voice.muted}
      connectionLost={voice.connectionLost}
      canInterrupt={view.canInterrupt}
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
    <VoiceSettings
      {arabic}
      voices={catalog.voices}
      bind:voiceId={catalog.voiceId}
      bind:voiceSearch={catalog.search}
      nextVoicePage={catalog.nextPage}
      voiceLoading={catalog.loading}
      voiceError={catalog.error}
      voiceSaving={catalog.saving}
      settingsError={catalog.settingsError}
      live={voice.live !== null}
      {selectedVoice}
      {sessionVoice}
      confirmedVoice={null}
      status={inputVoiceStatus}
      {captionsVisible}
      onsearch={() => void catalogStore!.load()}
      onloadmore={() => void catalogStore!.load(true)}
      onremember={() => void catalogStore!.remember()}
      ontoggleCaptions={toggleCaptions}
    />
  {/snippet}
</AppShell>

<p class="sr-only" aria-live="polite" aria-atomic="true">{announcement}</p>
