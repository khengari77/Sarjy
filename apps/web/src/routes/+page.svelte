<script lang="ts">
  import { onMount, tick } from 'svelte';
  import {
    request,
    type Conversation,
    type Turn,
    type SessionConfig,
    type StartVoice,
    type VoiceOption,
    type VoiceCatalog
  } from '../lib/api';
  import type { VoiceRuntime } from '../lib/audio/voice-runtime';
  import { createVoiceRuntime } from '../lib/audio/runtime-factory';
  import {
    friendlyVoiceError,
    isFatalVoiceError,
    phaseFromAgentState,
    phaseFromTransportStatus
  } from '../lib/audio/livekitAdapter';
  import { ensureMicrophoneAccess, microphoneErrorCode } from '../lib/audio/microphone';
  import VoiceOrb from '../lib/orb/VoiceOrb.svelte';
  import { createEnvelopeMeter, type EnvelopeMeter } from '../lib/orb/audioEnvelope';
  import LiveCaptionPreview from '../lib/chat/LiveCaptionPreview.svelte';
  import TranscriptDrawer from '../lib/chat/TranscriptDrawer.svelte';
  import ToolTraceChip from '../lib/chat/ToolTraceChip.svelte';
  import { deriveOrbPresentation, INITIAL_STATE, type VoicePhase } from '../lib/orb/orbState';
  import AppShell from '../lib/ui/AppShell.svelte';
  import ChatHistorySidebar from '../lib/history/ChatHistorySidebar.svelte';

  // English-first release: the localization code and Arabic copy stay in place,
  // but the reviewer-facing interface is fixed to English until the switch is
  // deliberately re-enabled.
  const LANGUAGE_SWITCH_ENABLED = false;
  let arabic = $state(false);
  let voiceId = $state('');
  let voices = $state<VoiceOption[]>([]);
  let voiceSearch = $state('');
  let loadedVoiceSearch = '';
  let nextVoicePage = $state<string | null>(null);
  let voiceLoading = $state(false);
  let voiceError = $state('');
  let activeVoiceName = $state('');

  let conversations = $state<Conversation[]>([]);
  let toolCalls = $state<{ id: string; name: string; result: unknown }[]>([]);
  let current = $state<string | null>(null);
  let turns = $state<Turn[]>([]);
  let draft = $state('');
  let transportStatus = $state('idle');
  let agentState = $state<string | null>(null);
  let listError = $state('');
  let loadingConversations = $state(true);
  let pendingIntent = $state<{ type: 'switch'; id: string } | { type: 'create' } | null>(null);
  let selectionEpoch = 0;
  let switchCancel: HTMLButtonElement | undefined = $state();
  let transcriptButton: HTMLButtonElement | undefined = $state();
  let announcement = $state('');
  let devOverlay = $state<'none' | 'memory_work' | 'confirming' | 'muted' | 'error'>('none');
  let lastAnnounced = '';
  let voiceErrorCode = $state('');
  let busy = $state(false);
  let connecting = $state(false);
  let connectionLost = $state(false);
  let muted = $state(true);
  let audioBlocked = $state(false);
  let live = $state<SessionConfig | null>(null);
  let micLevel = $state(0);
  let outputLevel = $state(0);
  let captionsVisible = $state(true);
  let transcriptOpen = $state(false);
  let provisional = $state<{ id: string; text: string; speaker: 'user' | 'assistant' } | null>(null);
  let micMeter: EnvelopeMeter | null = null;
  let meterTimer: ReturnType<typeof setInterval> | null = null;
  let runtime: VoiceRuntime;
  let connectAttempt = 0;
  let ending = false;

  const words = $derived(
    arabic
      ? {
          new: 'محادثة جديدة',
          title: 'تحدث. تذكّر. تحكّم بالسياق.',
          intro: 'مساعدك الصوتي، وسياقه بين يديك.',
          placeholder: 'اكتب رسالتك…',
          send: 'إرسال',
          call: 'بدء مكالمة',
          end: 'إنهاء',
          mute: 'كتم الصوت',
          unmute: 'تشغيل الميكروفون'
        }
      : {
          new: 'New conversation',
          title: 'Speak. Remember. Shape the context.',
          intro: 'Your voice assistant, with context you control.',
          placeholder: 'Write a message…',
          send: 'Send',
          call: 'Start call',
          end: 'End call',
          mute: 'Mute',
          unmute: 'Enable microphone'
        }
  );

  const phase = $derived<VoicePhase>(
    (agentState ? phaseFromAgentState(agentState) : null) ??
      phaseFromTransportStatus(transportStatus) ??
      'idle'
  );

  const canInterrupt = $derived(phase === 'thinking' || phase === 'speaking');
  const fatalError = $derived(voiceErrorCode !== '' && isFatalVoiceError(voiceErrorCode));
  const voiceErrorText = $derived(voiceErrorCode ? friendlyVoiceError(voiceErrorCode, arabic) : '');

  const presentation = $derived(
    deriveOrbPresentation({
      ...INITIAL_STATE,
      phase,
      overlay: devOverlay !== 'none' ? devOverlay : fatalError ? 'error' : live && muted ? 'muted' : 'none',
      micActive: live !== null && !muted,
      sessionId: live?.id ?? null,
      connectionReady: live !== null && !connectionLost
    })
  );

  async function loadVoices(more = false) {
    if (voiceLoading) return;
    voiceLoading = true;
    voiceError = '';
    try {
      const search = more ? loadedVoiceSearch : voiceSearch;
      const query = new URLSearchParams({ search });
      if (more && nextVoicePage) query.set('next_page_token', nextVoicePage);
      const result = await request<VoiceCatalog>('voices?' + query);
      voices = more
        ? [...new Map([...voices, ...result.voices].map((voice) => [voice.id, voice])).values()]
        : result.voices;
      nextVoicePage = result.next_page_token ?? null;
      loadedVoiceSearch = search;
    } catch (e) {
      voiceError = e instanceof Error ? e.message : 'VOICE_CATALOG_UNAVAILABLE';
    } finally {
      voiceLoading = false;
    }
  }

  function rememberVoice() {
    localStorage.setItem('sarjy-voice-id', voiceId);
  }

  async function refresh() {
    const id = current;
    const epoch = ++selectionEpoch;
    if (id) {
      const [history, tools] = await Promise.all([
        request<Turn[]>('conversations/' + id + '/turns'),
        request<typeof toolCalls>('conversations/' + id + '/tool-calls')
      ]);
      // Ignore a response that was overtaken by a newer selection.
      if (current === id && epoch === selectionEpoch) {
        turns = history;
        toolCalls = tools;
      }
    }
  }

  async function reloadHistory() {
    listError = '';
    loadingConversations = true;
    try {
      conversations = await request<Conversation[]>('conversations');
    } finally {
      loadingConversations = false;
    }
  }

  $effect(() => {
    if (pendingIntent) switchCancel?.focus();
  });

  $effect(() => {
    const label = arabic ? presentation.statusLabel.ar : presentation.statusLabel.en;
    if (label === lastAnnounced) return;
    const timer = setTimeout(() => {
      lastAnnounced = label;
      announcement = label;
    }, 400);
    return () => clearTimeout(timer);
  });

  async function openTranscript() {
    transcriptOpen = true;
  }

  async function closeTranscript() {
    transcriptOpen = false;
    await tick();
    transcriptButton?.focus();
  }

  function requestSelect(id: string) {
    if (id === current) return;
    if (live) {
      pendingIntent = { type: 'switch', id };
      return;
    }
    void run(() => select(id));
  }

  async function confirmSwitch() {
    const intent = pendingIntent;
    pendingIntent = null;
    if (!intent) return;
    await run(async () => {
      await end();
      if (intent.type === 'create') {
        await createConversation();
      } else {
        current = intent.id;
        await refresh();
      }
    });
  }

  function errorCode(error: unknown): string {
    if (error instanceof Error) {
      if (/^[A-Z0-9_]+$/.test(error.message)) return error.message;
      if (error.name === 'NotAllowedError' || error.name === 'SecurityError') return 'MICROPHONE_PERMISSION_DENIED';
      if (error.name === 'NotFoundError' || error.name === 'OverconstrainedError') return 'MICROPHONE_NOT_FOUND';
    }
    return 'API_UNAVAILABLE';
  }

  async function run(work: () => Promise<void>, onError?: (error: unknown) => void) {
    busy = true;
    try {
      await work();
    } catch (e) {
      if (onError) onError(e);
      else listError = errorCode(e);
    } finally {
      busy = false;
    }
  }

  async function end() {
    if (ending) return;
    ending = true;
    connectAttempt += 1;
    const session = live;
    teardownMeters();
    live = null;
    muted = true;
    connectionLost = false;
    provisional = null;
    transportStatus = 'idle';
    agentState = null;
    try {
      await runtime?.disconnect();
      if (session) await request('voice/sessions/' + session.id + '/end', {}).catch(() => {});
      await refresh().catch(() => {});
    } finally {
      ending = false;
    }
  }

  async function select(id: string) {
    if (live) await end();
    current = id;
    await refresh();
  }

  async function createConversation() {
    const chat = await request<Conversation>('conversations', {});
    conversations = [chat, ...conversations];
    await select(chat.id);
  }

  async function create() {
    // Starting a new conversation ends any live call, so it takes the same
    // explicit confirmation as switching chats.
    if (live) {
      pendingIntent = { type: 'create' };
      return;
    }
    await run(createConversation);
  }

  async function connect(microphone: boolean) {
    if (live) {
      if (microphone && muted) await toggleMute();
      return;
    }
    if (connecting) return;

    const attempt = ++connectAttempt;
    connecting = true;
    voiceErrorCode = '';
    listError = '';
    connectionLost = false;

    try {
      // Starting voice is an explicit gesture: create a conversation lazily
      // when the history is empty instead of creating one on page load.
      if (!current) {
        const chat = await request<Conversation>('conversations', {});
        conversations = [chat, ...conversations];
        current = chat.id;
      }

      // Ask for the microphone before creating a paid session so a denial
      // cannot consume capacity or leave a half-open room.
      if (microphone) {
        try {
          await ensureMicrophoneAccess();
        } catch (error) {
          voiceErrorCode = errorCode(error);
          return;
        }
      }

      const requestedVoiceId = voiceId;
      const requestedVoiceName = voices.find((voice) => voice.id === requestedVoiceId)?.name || requestedVoiceId;
      const config = await request<SessionConfig>('voice/sessions', {
        conversation_id: current,
        language: arabic ? 'ar' : 'en',
        voice_id: requestedVoiceId || null,
        response_preference: 'automatic'
      } satisfies StartVoice);
      if (attempt !== connectAttempt) {
        // The user cancelled while the session request was in flight.
        await request('voice/sessions/' + config.id + '/end', {}).catch(() => {});
        return;
      }

      try {
        transportStatus = 'connecting';
        agentState = null;
        await runtime.connect(config);
        if (attempt !== connectAttempt) {
          await runtime.disconnect();
          await request('voice/sessions/' + config.id + '/end', {}).catch(() => {});
          return;
        }
        live = config;
        activeVoiceName = requestedVoiceName;
        muted = !microphone;
        if (microphone) await runtime.startMicrophone();
        // Playback metering always starts with the session; microphone
        // metering is conditional inside setupMeters().
        setupMeters();
      } catch (error) {
        await runtime.disconnect();
        await request('voice/sessions/' + config.id + '/end', {}).catch(() => {});
        transportStatus = 'disconnected';
        agentState = null;
        voiceErrorCode = errorCode(error);
      }
    } catch (error) {
      voiceErrorCode = errorCode(error);
      transportStatus = 'disconnected';
    } finally {
      if (attempt === connectAttempt) connecting = false;
    }
  }

  async function cancelConnect() {
    connectAttempt += 1;
    connecting = false;
    voiceErrorCode = '';
    transportStatus = 'idle';
    agentState = null;
    await runtime.cancelConnect().catch(() => {});
  }

  async function reconnect() {
    await end();
    await connect(true);
  }

  async function interrupt() {
    if (!live || !canInterrupt) return;
    await run(
      async () => {
        await runtime.interrupt();
        agentState = 'listening';
      },
      (error) => {
        voiceErrorCode = errorCode(error);
      }
    );
  }

  async function send() {
    if (!draft.trim()) return;
    await run(async () => {
      if (!live) await connect(false);
      if (!live) return;
      await runtime.sendText(draft.trim());
      draft = '';
    });
  }

  async function toggleMute() {
    await run(
      async () => {
        if (!live) return;
        if (muted) {
          await runtime.startMicrophone();
          muted = false;
          startMicMeter();
        } else {
          await runtime.stopMicrophone();
          muted = true;
          stopMicMeter();
        }
      },
      (error) => {
        voiceErrorCode = errorCode(error);
      }
    );
  }

  function stopMicMeter() {
    micMeter?.dispose();
    micMeter = null;
    micLevel = 0;
  }

  function startMicMeter() {
    stopMicMeter();
    if (!live || muted) return;
    const stream = runtime.microphoneStream?.();
    if (!stream) return;
    micMeter = createEnvelopeMeter(stream);
  }

  function stopOutputMeter() {
    if (meterTimer) clearInterval(meterTimer);
    meterTimer = null;
    outputLevel = 0;
  }

  // Playback metering belongs to the live session, not to the microphone.
  // Muting only detaches the microphone meter; the assistant envelope keeps
  // following the remote audio so the orb still reflects speaking.
  function startOutputMeter() {
    if (meterTimer) return;
    meterTimer = setInterval(() => {
      micLevel = micMeter?.level() ?? 0;
      outputLevel = runtime.remoteAudioLevel?.() ?? 0;
    }, 80);
  }

  function teardownMeters() {
    stopMicMeter();
    stopOutputMeter();
  }

  function setupMeters() {
    startMicMeter();
    startOutputMeter();
  }

  onMount(() => {
    // DEV-only visual fixture hook; production always uses live state.
    if (import.meta.env.DEV) {
      const params = new URLSearchParams(location.search);
      const fixture = params.get('phase');
      const overlay = params.get('overlay');
      if (fixture === 'idle' || fixture === 'disconnected' || fixture === 'connecting') transportStatus = fixture;
      else if (fixture === 'listening' || fixture === 'thinking' || fixture === 'speaking') agentState = fixture;
      if (overlay === 'memory_work' || overlay === 'confirming' || overlay === 'muted' || overlay === 'error') devOverlay = overlay;
    }
    // The saved preference is retained for a future release, but it is not
    // applied while the switch is hidden: an old Arabic preference must not
    // silently start an Arabic session during the English-first release.
    if (LANGUAGE_SWITCH_ENABLED) {
      arabic = localStorage.getItem('sarjy-language') === 'ar';
    }
    voiceId = localStorage.getItem('sarjy-voice-id') || '';
    captionsVisible = localStorage.getItem('sarjy-captions') !== 'off';
    runtime = createVoiceRuntime();
    const unsubscribe = runtime.subscribe((event) => {
      // Ignore callbacks from a room the user already replaced or cancelled.
      if (event.epoch !== runtime.epoch) return;
      if (event.type === 'status') {
        if (event.source === 'agent') {
          agentState = event.text;
        } else {
          agentState = null;
          transportStatus = event.text;
          if (event.text === 'disconnected' && live && !ending) {
            // Transport loss is terminal for this room. Keep the session id so
            // End can release it, but never auto-create a replacement.
            teardownMeters();
            muted = true;
            connectionLost = true;
            provisional = null;
          }
        }
      }
      if (event.type === 'error') voiceErrorCode = event.code;
      if (event.type === 'audio-blocked') audioBlocked = true;
      if (event.type === 'transcript') {
        if (event.final) {
          provisional = null;
          void refresh().catch(() => {});
        } else {
          provisional = { id: event.segmentId, text: event.text, speaker: event.speaker };
        }
      }
    });
    void run(async () => {
      await request('demo-session', {});
      await reloadHistory();
      // Never create a conversation implicitly: the empty state owns that
      // explicit action so the user always knows when a chat is started.
      if (conversations.length) await select(conversations[0].id);
    });
    const endOnPageHide = () => {
      if (!live) return;
      try {
        void fetch('/api/v1/voice/sessions/' + live.id + '/end', {
          method: 'POST',
          keepalive: true,
          headers: { 'content-type': 'application/json' },
          body: '{}'
        });
      } catch {
        /* best effort: worker departure remains the fallback */
      }
    };
    window.addEventListener('pagehide', endOnPageHide);
    const poll = setInterval(() => {
      if (live && Date.parse(live.expires_at) <= Date.now()) void run(end);
      else if (live) void refresh().catch(() => {});
    }, 2500);
    return () => {
      teardownMeters();
      window.removeEventListener('pagehide', endOnPageHide);
      clearInterval(poll);
      unsubscribe();
      void runtime.disconnect();
      if (live) void request('voice/sessions/' + live.id + '/end', {}).catch(() => {});
    };
  });

  function toggleCaptions() {
    captionsVisible = !captionsVisible;
    localStorage.setItem('sarjy-captions', captionsVisible ? 'on' : 'off');
  }

  function toggleLanguage() {
    arabic = !arabic;
    localStorage.setItem('sarjy-language', arabic ? 'ar' : 'en');
  }

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
    <ChatHistorySidebar
      {conversations}
      {current}
      {arabic}
      {busy}
      loading={loadingConversations}
      error={listError}
      onselect={requestSelect}
      oncreate={create}
      onretry={() => run(reloadHistory)}
    />
  {/snippet}

  {#snippet stage()}
    <section class="voice-stage" dir={arabic ? 'rtl' : 'ltr'}>
      {#if pendingIntent}
        <div class="confirm-scrim" role="presentation">
          <div
            class="confirm-dialog"
            role="dialog"
            aria-modal="true"
            tabindex="-1"
            aria-labelledby="switch-title"
            onkeydown={(event) => {
              if (event.key === 'Escape') pendingIntent = null;
            }}
          >
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
              <button class="ghost-button" bind:this={switchCancel} onclick={() => (pendingIntent = null)}>
                {arabic ? 'إلغاء' : 'Cancel'}
              </button>
              <button class="primary-button" onclick={confirmSwitch}>
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
            <button class="ghost-button" disabled={busy} onclick={() => void connect(true)}>
              {arabic ? 'إعادة محاولة الميكروفون' : 'Retry microphone'}
            </button>
          {/if}
        </div>
      {/if}

      {#if connecting}
        <button class="ghost-button" onclick={cancelConnect}>
          {arabic ? 'إلغاء الاتصال' : 'Cancel connecting'}
        </button>
      {/if}

      {#if connectionLost && live}
        <div class="alert" role="status" data-code="CONNECTION_LOST">
          <p>{arabic ? 'انقطع الاتصال.' : 'Connection lost.'}</p>
          <button class="ghost-button" disabled={busy} onclick={() => void reconnect()}>
            {arabic ? 'إعادة الاتصال' : 'Reconnect'}
          </button>
          <button class="ghost-button" disabled={busy} onclick={() => run(end)}>
            {arabic ? 'إنهاء' : 'End'}
          </button>
        </div>
      {/if}

      {#if audioBlocked}
        <button
          class="primary-button"
          onclick={() =>
            run(async () => {
              await runtime.resumeAudio?.();
              audioBlocked = false;
            })}
        >
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
          onactivate={() => {
            if (live) {
              if (muted) void toggleMute();
              else if (canInterrupt) void interrupt();
              return;
            }
            void connect(true);
          }}
        />
        {#if !live && !turns.length && !provisional}
          <h1>{words.title}</h1>
          <p class="intro">{words.intro}</p>
        {/if}
      </div>

      {#if turns.length || provisional}
        <LiveCaptionPreview
          {turns}
          {provisional}
          {arabic}
          {captionsVisible}
          expanded={transcriptOpen}
          onexpand={openTranscript}
        />
        {#if toolCalls.length}
          <div class="trace-row">
            {#each toolCalls as tool}
              <ToolTraceChip name={tool.name} result={tool.result} {arabic} />
            {/each}
          </div>
        {/if}
      {/if}

      {#if live || turns.length || provisional}
        <button class="ghost-button transcript-trigger" bind:this={transcriptButton} type="button" onclick={openTranscript}>
          {arabic ? 'سجل المحادثة' : 'Transcript'}
        </button>
      {/if}

    </section>

    <div class="controls">
      {#if live}
        <button class="primary-button" disabled={busy} onclick={() => run(end)}>{words.end}</button>
        <button class="ghost-button" disabled={busy || connectionLost} onclick={toggleMute}>{muted ? words.unmute : words.mute}</button>
        <button class="ghost-button" disabled={busy || !canInterrupt} onclick={() => void interrupt()}>
          {arabic ? 'إيقاف' : 'Stop'}
        </button>
      {:else if connecting}
        <button class="ghost-button" onclick={cancelConnect}>{arabic ? 'إلغاء' : 'Cancel'}</button>
      {:else}
        <button class="primary-button" disabled={busy} onclick={() => void connect(true)}>
          {words.call}
        </button>
      {/if}
    </div>

    <div class="composer">
      <textarea bind:value={draft} aria-label={words.placeholder} placeholder={words.placeholder} disabled={busy} rows="1"></textarea>
      <button class="primary-button" disabled={busy || !draft.trim()} onclick={send}>{words.send} ↗</button>
    </div>
    <small class="attribution"><a href="https://open-meteo.com/">Weather data: Open-Meteo</a></small>
  {/snippet}

  {#snippet right()}
    <div class="sidebar-inner" dir={arabic ? 'rtl' : 'ltr'}>
      <section class="panel-block">
        <h2>{arabic ? 'الذاكرة والسياق' : 'Memory & Context'}</h2>
        <p class="muted-text">
          {arabic
            ? 'ابحث في ما يتذكّره سَرْجي وراجع السياق قبل استخدامه.'
            : 'Search what Sarjy remembers and review context before it is used.'}
        </p>
      </section>

      <section class="panel-block">
        <h2>{arabic ? 'الصوت' : 'Voice'}</h2>
        <form
          class="voice-search"
          onsubmit={(event) => {
            event.preventDefault();
            void loadVoices();
          }}
        >
          <input
            aria-label={arabic ? 'البحث عن صوت' : 'Search voices'}
            bind:value={voiceSearch}
            maxlength="120"
            placeholder={arabic ? 'ابحث باسم الصوت أو اللغة' : 'Search by voice name or language'}
          />
          <button type="submit" class="ghost-button" disabled={voiceLoading}>
            {arabic ? 'البحث' : 'Load voices / Search'}
          </button>
        </form>
        <label class="field-label" for="assistant-voice">{arabic ? 'اختر الصوت' : 'Choose voice'}</label>
        <select id="assistant-voice" bind:value={voiceId} onchange={rememberVoice}>
          <option value="">{arabic ? 'اختر صوتًا قبل بدء المكالمة' : 'Select a voice before calling'}</option>
          {#if voiceId && !voices.some((voice) => voice.id === voiceId)}
            <option value={voiceId}>{arabic ? 'الصوت المحفوظ (أعد تحميل القائمة)' : 'Saved voice (reload list)'}</option>
          {/if}
          {#each voices as voice}
            <option value={voice.id}>{voice.name}{voice.languages?.length ? ' · ' + voice.languages.join(', ') : ''}</option>
          {/each}
        </select>
        {#if nextVoicePage}
          <button class="ghost-button" disabled={voiceLoading} onclick={() => loadVoices(true)}>
            {arabic ? 'المزيد' : 'Load more'}
          </button>
        {/if}
        {#if voiceError}
          <p class="voice-error">{arabic ? 'تعذّر تحميل الأصوات' : 'Could not load voices'}: {voiceError}</p>
        {/if}
        {#if voices.length === 0 && !voiceLoading && !voiceError}
          <small class="muted-text">
            {arabic
              ? 'حمّل الأصوات المتاحة لاختيار صوت مناسب للعربية.'
              : 'Load available voices to choose an English voice.'}
          </small>
        {/if}
        <small class="muted-text">
          {arabic
            ? 'التغييرات تسري على المكالمة التالية. تسميات اللغة لا تضمن جودة النطق.'
            : 'Changes apply to the next call. Language labels do not guarantee pronunciation quality.'}
        </small>
        {#if live}
          <small class="muted-text">{arabic ? 'صوت المكالمة الحالية' : 'Current call voice'}: {activeVoiceName}</small>
        {/if}
      </section>

      <section class="panel-block">
        <h2>{arabic ? 'التجربة' : 'Experience'}</h2>
        <label class="toggle-row">
          <input type="checkbox" checked={captionsVisible} onchange={toggleCaptions} />
          <span>{arabic ? 'إظهار الترجمة' : 'Show captions'}</span>
        </label>
        <p class="muted-text">
          {arabic ? 'سيُضاف التحكم بسرعة الكلام هنا.' : 'Speaking-speed controls will live here.'}
        </p>
      </section>
    </div>
  {/snippet}
</AppShell>

<TranscriptDrawer
  open={transcriptOpen}
  {turns}
  {provisional}
  tools={toolCalls}
  {arabic}
  onclose={closeTranscript}
/>

<p class="sr-only" aria-live="polite" aria-atomic="true">{announcement}</p>

<style>
  .ghost-button,
  .primary-button {
    font: inherit;
    border-radius: var(--radius-control);
    min-height: var(--control-height);
    padding: 10px 16px;
    border: 1px solid var(--border-subtle);
    color: var(--text-primary);
    background: transparent;
    cursor: pointer;
    transition:
      background var(--transition-quick),
      border-color var(--transition-quick),
      opacity var(--transition-quick);
  }

  .ghost-button:hover:enabled {
    background: var(--surface-hover);
  }

  .primary-button {
    background: color-mix(in srgb, var(--orb-idle) 18%, transparent);
    border-color: color-mix(in srgb, var(--orb-idle) 45%, var(--border-subtle));
    font-weight: 600;
  }

  .primary-button:hover:enabled {
    background: color-mix(in srgb, var(--orb-idle) 28%, transparent);
  }

  button:disabled {
    opacity: 0.55;
    cursor: not-allowed;
  }

  .sidebar-inner {
    display: flex;
    flex-direction: column;
    gap: 14px;
    padding: 18px 16px;
    min-height: 100%;
  }

  .voice-stage {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 18px;
    width: 100%;
    max-width: var(--stage-max-width);
    margin: 0 auto;
    padding: 32px 20px 12px;
    text-align: center;
  }

  .status-line {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    margin: 0;
    font-size: 13px;
    color: var(--text-muted);
    letter-spacing: 0.02em;
  }

  .status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    box-shadow: 0 0 10px currentColor;
    transition: background-color var(--transition-state);
  }

  .alert {
    margin: 0;
    padding: 10px 14px;
    border-radius: var(--radius-control);
    border: 1px solid color-mix(in srgb, var(--orb-error) 45%, transparent);
    background: color-mix(in srgb, var(--orb-error) 12%, transparent);
    color: #ffd9dd;
    max-width: 46ch;
  }

  .orb-zone {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 18px;
  }

  h1 {
    margin: 0;
    font-size: clamp(24px, 3.4vw, 40px);
    font-weight: 500;
    letter-spacing: -0.01em;
    max-width: 22ch;
    line-height: 1.2;
  }

  .intro {
    margin: 0;
    color: var(--text-muted);
    max-width: 46ch;
  }

  .trace-row {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    justify-content: center;
  }

  .toggle-row {
    display: flex;
    align-items: center;
    gap: 10px;
    min-height: 44px;
    font-size: 14px;
    color: var(--text-primary);
  }

  .toggle-row input {
    width: 20px;
    height: 20px;
    accent-color: var(--orb-idle);
  }

  .controls {
    display: flex;
    gap: 10px;
    justify-content: center;
    padding: 8px 20px;
    flex-wrap: wrap;
  }

  .composer {
    display: flex;
    gap: 10px;
    align-items: flex-end;
    width: 100%;
    max-width: var(--stage-max-width);
    margin: 0 auto;
    padding: 8px 20px calc(10px + env(safe-area-inset-bottom));
  }

  textarea {
    flex: 1;
    resize: none;
    min-height: var(--control-height);
    max-height: 140px;
    padding: 12px 14px;
    border-radius: var(--radius-control);
    border: 1px solid var(--border-subtle);
    background: var(--surface-elevated);
    color: var(--text-primary);
    font: inherit;
  }

  .attribution {
    display: block;
    text-align: center;
    padding-bottom: calc(12px + env(safe-area-inset-bottom));
    color: var(--text-muted);
    font-size: 11px;
  }

  a {
    color: inherit;
  }

  .panel-block {
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding-bottom: 18px;
    border-bottom: 1px solid var(--border-subtle);
  }

  .panel-block:last-child {
    border-bottom: 0;
  }

  .panel-block h2 {
    margin: 0;
    font-size: 12px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--text-muted);
  }

  .muted-text {
    margin: 0;
    color: var(--text-muted);
    font-size: 13px;
    line-height: 1.5;
  }

  .voice-search {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }

  .voice-search input,
  select {
    font: inherit;
    min-height: var(--control-height);
    padding: 10px 12px;
    border-radius: var(--radius-control);
    border: 1px solid var(--border-subtle);
    background: var(--surface-elevated);
    color: var(--text-primary);
    max-width: 100%;
  }

  .voice-search input {
    flex: 1;
    min-width: 120px;
  }

  .field-label {
    font-size: 12px;
    color: var(--text-muted);
  }

  .voice-error {
    margin: 0;
    color: #ffb3bb;
    font-size: 13px;
  }

  @media (max-width: 767px) {
    .voice-stage {
      padding: 20px 14px 8px;
    }

    .composer {
      padding-inline: 14px;
    }
  }

  .confirm-scrim {
    position: fixed;
    inset: 0;
    z-index: 50;
    display: grid;
    place-items: center;
    padding: 24px;
    background: rgb(0 0 0 / 0.55);
    backdrop-filter: blur(3px);
  }

  .confirm-dialog {
    width: min(420px, 100%);
    padding: 20px;
    border-radius: 14px;
    border: 1px solid var(--border-subtle);
    background: var(--surface-elevated);
    color: var(--text-primary);
  }

  .confirm-dialog h2 {
    margin: 0 0 8px;
    font-size: 18px;
  }

  .confirm-dialog p {
    margin: 0 0 16px;
    color: var(--text-muted);
    font-size: 14px;
    line-height: 1.5;
  }

  .confirm-actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
  }

</style>
