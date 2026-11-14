<script lang="ts">
  import { onMount } from 'svelte';
  import {
    friendlyVoiceError,
    isFatalVoiceError,
    phaseFromAgentState,
    phaseFromTransportStatus,
  } from '../lib/audio/livekitAdapter';
  import { deriveOrbPresentation, INITIAL_STATE, type VoicePhase } from '../lib/orb/orbState';
  import AppShell from '../lib/ui/AppShell.svelte';
  import ConversationList from '../lib/chat/ConversationList.svelte';
  import VoiceStage from '../lib/chat/VoiceStage.svelte';
  import type { PendingIntent } from '../lib/chat/intent';
  import CallControls from '../lib/chat/CallControls.svelte';
  import Composer from '../lib/chat/Composer.svelte';
  import VoiceSettings from '../lib/chat/VoiceSettings.svelte';
  import { wordsFor } from '../lib/chat/words';
  import { initialVoiceState, type VoiceConnection } from '../lib/voice/connection';
  import { errorCode } from '../lib/voice/errors';
  import { createCatalogStore, initialCatalogState } from '../lib/voice/catalog';
  import { initialConversationState, type ConversationStore } from '../lib/voice/conversations';
  import { bootStudio } from '../lib/voice/studio';
  import { createSelectionController } from '../lib/voice/selection';
  import { initialVoiceSwitchState } from '../lib/voice/voiceSwitch';
  import { createLiveVoiceSwitch } from '../lib/voice/voiceSwitchController';
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
  let voiceSwitch = $state(initialVoiceSwitchState());
  let liveSessionId = $state<string | null>(null);
  let connection: VoiceConnection | undefined;
  let conversations: ConversationStore | undefined;
  let catalogStore: ReturnType<typeof createCatalogStore> | undefined;
  let voiceActions = $state<ReturnType<typeof bootStudio>['voiceActions']>();
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
  const liveSwitch = createLiveVoiceSwitch({
    state: voiceSwitch,
    getSession: () => voice.live,
    getConversationId: () => conversation.current,
    getVoiceId: () => catalog.voiceId,
    getVoices: () => catalog.voices,
    selectVoice: (voiceId) => (catalog.voiceId = voiceId),
    remember: () => catalogStore!.remember(),
    createRequestId: () => crypto.randomUUID(),
  });

  // A new session (including reconnect) starts from the selected voice; live
  // pending/confirmed state never leaks across rooms.
  $effect(() => {
    const id = voice.live?.id ?? null;
    if (id === liveSessionId) return;
    liveSessionId = id;
    liveSwitch.onSession(id, voice.activeVoiceName);
  });

  const phase = $derived<VoicePhase>(
    (voice.agentState ? phaseFromAgentState(voice.agentState) : null) ??
      phaseFromTransportStatus(voice.transportStatus) ??
      'idle',
  );
  const canInterrupt = $derived(phase === 'thinking' || phase === 'speaking');
  const confirmedVoiceName = $derived(voiceSwitch.activeVoiceName || voice.activeVoiceName);
  const voiceSwitchError = $derived(voiceSwitch.errorCode ? friendlyVoiceError(voiceSwitch.errorCode, arabic) : '');
  const fatalError = $derived(voice.voiceErrorCode !== '' && isFatalVoiceError(voice.voiceErrorCode));
  const voiceErrorText = $derived(voice.voiceErrorCode ? friendlyVoiceError(voice.voiceErrorCode, arabic) : '');
  const presentation = $derived(
    deriveOrbPresentation({
      ...INITIAL_STATE,
      phase,
      overlay: devOverlay !== 'none' ? devOverlay : fatalError ? 'error' : voice.live && voice.muted ? 'muted' : 'none',
      micActive: voice.live !== null && !voice.muted,
      sessionId: voice.live?.id ?? null,
      connectionReady: voice.live !== null && !voice.connectionLost,
      micLevel: voice.micLevel,
      outputLevel: voice.outputLevel,
    }),
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

  function changeVoice() {
    void run(() => liveSwitch.change());
  }

  // A pending change only completes at the next synthesis boundary, so retry
  // the ACK read when a new turn starts instead of calling silence a failure.
  $effect(() => {
    if (voiceSwitch.status === 'pending' && (phase === 'thinking' || phase === 'speaking')) void liveSwitch.resume();
  });

  function toggleCaptions() {
    captionsVisible = !captionsVisible;
    localStorage.setItem('sarjy-captions', captionsVisible ? 'on' : 'off');
  }

  function toggleLanguage() {
    arabic = !arabic;
    localStorage.setItem('sarjy-language', arabic ? 'ar' : 'en');
  }

  $effect(() => {
    const label = arabic ? presentation.statusLabel.ar : presentation.statusLabel.en;
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
      getCanInterrupt: () => canInterrupt,
      run: (work, onError) => run(work, onError),
      onVoiceError,
    });
    connection = boot.connection;
    conversations = boot.conversations;
    catalogStore = boot.catalogStore;
    voiceActions = boot.voiceActions;
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
      onretrymic={() => voiceActions?.startConnect()}
      onreconnect={() => run(() => connection!.reconnect(), onVoiceError)}
      onend={() => run(() => connection!.end(), onVoiceError)}
      onenableaudio={() => void voiceActions?.enableAudio()}
      onorb={() => voiceActions?.handleOrb()}
      oncancelintent={() => (pendingIntent = null)}
      onconfirmintent={() => selection.confirmSwitch()}
    />
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
      ontoggle={() => void voiceActions?.toggleMute()}
      oninterrupt={() => void voiceActions?.interrupt()}
      onconnect={() => voiceActions?.startConnect()}
    />
    <Composer
      bind:draft
      placeholder={words.placeholder}
      sendLabel={words.send}
      disabled={busy}
      onsend={() => voiceActions?.sendDraft(draft, () => (draft = ''))}
    />
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
      live={voice.live}
      {confirmedVoiceName}
      switchStatus={voiceSwitch.status}
      switchError={voiceSwitchError}
      {captionsVisible}
      onsearch={() => void catalogStore!.load()}
      onloadmore={() => void catalogStore!.load(true)}
      onvoicechange={changeVoice}
      ontoggleCaptions={toggleCaptions}
    />
  {/snippet}
</AppShell>

<p class="sr-only" aria-live="polite" aria-atomic="true">{announcement}</p>
