<script lang="ts">
  import { onMount } from 'svelte';
  import type { VoiceRuntime } from '../lib/audio/voice-runtime';
  import { activateOrb } from '../lib/orb/orbActions';
  import type { ResolvedTheme } from '../lib/ui/theme';
  import type { ThemeControl } from '../lib/ui/themeSession.svelte';
  import AppShell from '../lib/ui/AppShell.svelte';
  import { revealTranscript } from '../lib/ui/revealTranscript';
  import HistoryPanel from '../lib/history/HistoryPanel.svelte';
  import VoiceStage from '../lib/chat/VoiceStage.svelte';
  import type { PendingIntent } from '../lib/chat/intent';
  import CallControls from '../lib/chat/CallControls.svelte';
  import Composer from '../lib/chat/Composer.svelte';
  import { selectedVoiceOption } from '../lib/chat/voice-state';
  import AppRightDrawer from '../lib/ui/AppRightDrawer.svelte';
  import { createAnnouncement } from '../lib/chat/announcement.svelte';
  import { wordsFor } from '../lib/chat/words';
  import { initialVoiceState, type VoiceConnection } from '../lib/voice/connection';
  import { errorCode, setVoiceError } from '../lib/voice/errors';
  import { storeLanguage } from '../lib/preferences';
  import { createCatalogStore, initialCatalogState } from '../lib/voice/catalog';
  import { initialConversationState, type ConversationStore } from '../lib/voice/conversations';
  import { deriveStage } from '../lib/voice/stageView';
  import { bootStudio } from '../lib/voice/studio';
  import { createSelectionController } from '../lib/voice/selection';
  import { createCommandSession } from '../lib/commands/commandSession.svelte';
  import CommandStatus from '../lib/commands/CommandStatus.svelte';
  import ChangeToast from '../lib/chat/ChangeToast.svelte';
  import WordleLauncher from '../lib/wordle/WordleLauncher.svelte';
  import '../lib/chat/chat.css';

  // English-first release: Arabic copy and provider plumbing stay in place but
  // the reviewer-facing interface is fixed to English.
  const LANGUAGE_SWITCH_ENABLED = false;
  let arabic = $state(false);
  let catalog = $state(initialCatalogState());
  let conversation = $state(initialConversationState());
  let draft = $state(''),
    pendingIntent = $state<PendingIntent>(null);
  let devOverlay = $state<'none' | 'memory_work' | 'confirming' | 'muted' | 'error'>('none'),
    busy = $state(false);
  // The shell owns storage and OS-preference resolution and reports the answer
  // through onthemechange. `dark` matches the tokens.css SSR fallback, so the
  // server and the first client frame agree before the boot script resolves.
  let theme = $state<ResolvedTheme>('dark');
  let voice = $state(initialVoiceState());
  // Reactive: children (the Wordle launcher) receive it as a prop and it is assigned after first render.
  let runtime = $state.raw<VoiceRuntime | undefined>(undefined);
  let connection: VoiceConnection | undefined;
  let conversations: ConversationStore | undefined, catalogStore: ReturnType<typeof createCatalogStore> | undefined;
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
  // Any finalised turn can save a memory server-side; the panel uses this to
  // invalidate a list it loaded before the save.
  // Panel state is a top-level reactive object so the shell bindings and the
  // command store share one source of truth.
  const panels = $state({ history: false, memory_explorer: false });
  // The control that opened the drawer on its Transcript tab; focus returns there.
  let drawerOpener = $state<HTMLElement | null>(null);
  const openTranscript = (opener: HTMLElement) =>
    revealTranscript(opener, panels, commands.state, (control) => (drawerOpener = control));

  // The shell owns the theme session and hands back its setter; a voice theme
  // command applies through it once and persists in this browser only.
  let shellTheme: ThemeControl | null = null;
  const commands = createCommandSession(catalog, panels, { setTheme: (preference) => shellTheme?.set(preference) });

  // One projection owns the phase, copy and orb presentation; the orb button
  // dispatches whatever action that presentation advertises.
  const view = $derived(deriveStage(voice, arabic, devOverlay, theme));
  const presentation = $derived(view.presentation);

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

  const onVoiceError = (error: unknown) => setVoiceError(voice, errorCode(error));

  function handleOrb() {
    activateOrb(presentation, {
      start: startConnect,
      cancel: () => void run(() => connection!.cancelConnect(), onVoiceError),
      mute: () => void toggleMute(),
      interrupt: () => void interrupt(),
      retry: () => (voice.live ? reconnect() : startConnect()),
      reconnect,
      end: () => void run(() => connection!.end(), onVoiceError),
    });
  }

  /** The composer mic mirrors the orb: start the call, or mute/unmute a live one. */
  function handleMic() {
    if (voice.live) void toggleMute();
    else if (!voice.connecting) startConnect();
  }

  const reconnect = () => void run(() => connection!.reconnect(), onVoiceError);
  const startConnect = () => void connection!.connect(true).catch(onVoiceError);
  const retryMicrophone = () => run(() => connection!.retryMicrophone(), onVoiceError);
  const toggleMute = () => run(() => connection!.toggleMute(), onVoiceError);

  const interrupt = () =>
    voice.live && view.canInterrupt ? run(() => connection!.interrupt(), onVoiceError) : undefined;

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

  const toggleLanguage = () => void storeLanguage((arabic = !arabic));

  const announcement = createAnnouncement(() => (arabic ? presentation.statusLabel.ar : presentation.statusLabel.en));

  onMount(() => {
    const boot = bootStudio({
      voice,
      catalog,
      conversation,
      restoreLanguage: LANGUAGE_SWITCH_ENABLED,
      setArabic: (value) => (arabic = value),
      hydrateSettings: (settings) => commands.hydrate(settings),
      applyCommand: commands.apply,
      newConversation: () => selection.create(),
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

<svelte:head><title>Sarjy — Voice Context Studio</title></svelte:head>

<AppShell
  bind:leftOpen={panels.history}
  bind:rightOpen={panels.memory_explorer}
  bind:rightOpener={drawerOpener}
  dir={arabic ? 'rtl' : 'ltr'}
  stageDialogOpen={pendingIntent !== null}
  languageLabel={arabic ? 'English' : 'العربية'}
  onLanguage={LANGUAGE_SWITCH_ENABLED ? toggleLanguage : undefined}
  leftLabel={arabic ? 'المحادثات' : 'Conversations'}
  rightLabel={arabic ? 'الذاكرة والإعدادات' : 'Memory and settings'}
  onthemechange={(value) => (theme = value)}
  onthemeready={(control) => (shellTheme = control)}
>
  {#snippet left()}
    <HistoryPanel
      {conversation}
      {voice}
      {arabic}
      {busy}
      {selection}
      onreload={() => run(() => conversations!.reload())}
    />
  {/snippet}

  {#snippet stage()}
    <WordleLauncher compact {runtime} />
    <VoiceStage
      {presentation}
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
      greeting={voice.greeting}
      captionsVisible={commands.state.settings.captions_visible}
      {busy}
      {words}
      onretrymic={retryMicrophone}
      onreconnect={() => run(() => connection!.reconnect(), onVoiceError)}
      onend={() => run(() => connection!.end(), onVoiceError)}
      onenableaudio={enableAudio}
      onorb={handleOrb}
      ontranscript={openTranscript}
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
      canInterrupt={view.canInterrupt}
      cleanupRequired={voice.pendingCleanup !== null}
      {words}
      onretrycleanup={() => run(() => connection!.retryCleanup(), onVoiceError)}
      onend={() => run(() => connection!.end(), onVoiceError)}
      oncancel={() => run(() => connection!.cancelConnect(), onVoiceError)}
      ontoggle={toggleMute}
      oninterrupt={interrupt}
    />
    <Composer
      bind:draft
      placeholder={words.placeholder}
      sendLabel={words.send}
      disabled={busy}
      onsend={send}
      micLabel={voice.live ? (voice.muted ? words.micOff : words.micOn) : words.talk}
      micPressed={voice.live ? !voice.muted : null}
      onmic={handleMic}
    />
  {/snippet}

  {#snippet right()}
    <AppRightDrawer
      {arabic}
      conversationId={conversation.current}
      memoryRevision={`${conversation.current ?? ''}:${conversation.turns.length}:${commands.memoryRevision}`}
      bind:activeTab={commands.state.panelTab}
      turns={conversation.turns}
      provisional={voice.provisional}
      tools={conversation.toolCalls}
      {catalog}
      selectedVoice={selectedVoiceOption(catalog.voices, catalog.voiceId)}
      sessionVoice={voice.live?.voice ?? null}
      live={voice.live !== null}
      speakingSpeed={commands.state.settings.speaking_speed}
      captionsVisible={commands.state.settings.captions_visible}
      memory={commands.memory}
      onsearch={() => void catalogStore!.load()}
      onloadmore={() => void catalogStore!.load(true)}
      onremember={() => void catalogStore!.remember()}
      onspeed={(speed) => commands.setSpeakingSpeed(speed)}
      ontoggleCaptions={() => void commands.setCaptions(!commands.state.settings.captions_visible)}
      onmemorymode={(mode) => void commands.setMemoryMode(mode)}
    />
  {/snippet}
</AppShell>

<p class="sr-only" aria-live="polite" aria-atomic="true">{announcement.message}</p>
<CommandStatus session={commands} />
<ChangeToast session={commands} {arabic} />
