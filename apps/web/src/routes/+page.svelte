<script lang="ts">
  import { onMount } from 'svelte';
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
  let voiceErrorCode = $state('');
  let busy = $state(false);
  let connecting = $state(false);
  let connectionLost = $state(false);
  let muted = $state(true);
  let audioBlocked = $state(false);
  let live = $state<SessionConfig | null>(null);
  let micLevel = $state(0);
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
      overlay: fatalError ? 'error' : live && muted ? 'muted' : 'none',
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
    if (id) {
      const [history, tools] = await Promise.all([
        request<Turn[]>('conversations/' + id + '/turns'),
        request<typeof toolCalls>('conversations/' + id + '/tool-calls')
      ]);
      if (current === id) {
        turns = history;
        toolCalls = tools;
      }
    }
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

  async function create() {
    await run(async () => {
      const chat = await request<Conversation>('conversations', {});
      conversations = [chat, ...conversations];
      await select(chat.id);
    });
  }

  async function connect(microphone: boolean) {
    if (!current) {
      voiceErrorCode = 'CONVERSATION_REQUIRED';
      return;
    }
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
        if (microphone) {
          await runtime.startMicrophone();
          setupMeters();
        }
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
          setupMeters();
        } else {
          await runtime.stopMicrophone();
          muted = true;
          teardownMeters();
        }
      },
      (error) => {
        voiceErrorCode = errorCode(error);
      }
    );
  }

  function teardownMeters() {
    micMeter?.dispose();
    micMeter = null;
    if (meterTimer) clearInterval(meterTimer);
    meterTimer = null;
    micLevel = 0;
  }

  function setupMeters() {
    teardownMeters();
    if (!live || muted) return;
    const stream = runtime.microphoneStream?.();
    if (!stream) return;
    micMeter = createEnvelopeMeter(stream);
    meterTimer = setInterval(() => {
      micLevel = micMeter?.level() ?? 0;
    }, 80);
  }

  onMount(() => {
    arabic = localStorage.getItem('sarjy-language') === 'ar';
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
      conversations = await request<Conversation[]>('conversations');
      if (conversations.length) await select(conversations[0].id);
      else {
        const chat = await request<Conversation>('conversations', {});
        conversations = [chat];
        current = chat.id;
      }
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
  <meta name="description" content="A bilingual voice assistant with transparent context." />
</svelte:head>

<AppShell
  dir={arabic ? 'rtl' : 'ltr'}
  modeLabel={arabic ? 'بسيط' : 'Simple'}
  languageLabel={arabic ? 'English' : 'العربية'}
  onLanguage={toggleLanguage}
  leftLabel={arabic ? 'المحادثات' : 'Conversations'}
  rightLabel={arabic ? 'الذاكرة والإعدادات' : 'Memory and settings'}
>

  {#snippet left()}
    <div class="sidebar-inner">
      <button class="primary-button" disabled={busy} onclick={create}>{words.new} +</button>
      <p class="sidebar-label">{arabic ? 'المحادثات' : 'Conversations'}</p>
      {#if listError}
        <p class="panel-error" role="alert" data-code={listError}>
          {arabic ? 'تعذّر تحميل المحادثات.' : 'Could not load conversations.'}
          <button class="ghost-button" onclick={() => run(refresh)}>{arabic ? 'إعادة المحاولة' : 'Retry'}</button>
        </p>
      {/if}
      <nav class="chat-list" aria-label={arabic ? 'قائمة المحادثات' : 'Conversation list'}>
        {#each conversations as chat}
          <button class="chat-item" class:chosen={current === chat.id} disabled={busy} onclick={() => run(() => select(chat.id))}>
            {chat.title}
          </button>
        {/each}
      </nav>
      <p class="sidebar-foot">Voice Context Studio</p>
    </div>
  {/snippet}

  {#snippet stage()}
    <section class="voice-stage" dir={arabic ? 'rtl' : 'ltr'}>
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

      {#if turns.length}
        <LiveCaptionPreview
          {turns}
          {arabic}
          {captionsVisible}
          expanded={transcriptOpen}
          onexpand={() => (transcriptOpen = true)}
        />
        {#if toolCalls.length}
          <div class="trace-row">
            {#each toolCalls as tool}
              <ToolTraceChip name={tool.name} {arabic} />
            {/each}
          </div>
        {/if}
      {:else}
        <div class="idle-block">
          <VoiceOrb
            {presentation}
            {micLevel}
            label={arabic ? presentation.ariaLabel.ar : presentation.ariaLabel.en}
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
          <h1>{words.title}</h1>
          <p class="intro">{words.intro}</p>
        </div>
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
        <button class="primary-button" disabled={busy || !current} onclick={() => void connect(true)}>
          {words.call}
        </button>
      {/if}
    </div>

    <div class="composer">
      <textarea bind:value={draft} aria-label={words.placeholder} placeholder={words.placeholder} disabled={busy || !current} rows="1"></textarea>
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
  tools={toolCalls}
  {arabic}
  onclose={() => (transcriptOpen = false)}
/>

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

  .sidebar-label {
    margin: 8px 0 0;
    font-size: 11px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--text-muted);
  }

  .chat-list {
    display: flex;
    flex-direction: column;
    gap: 6px;
    overflow-y: auto;
  }

  .chat-item {
    font: inherit;
    text-align: start;
    padding: 10px 12px;
    min-height: 40px;
    border-radius: var(--radius-control);
    border: 1px solid transparent;
    background: transparent;
    color: var(--text-muted);
    cursor: pointer;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .chat-item:hover {
    background: var(--surface-hover);
    color: var(--text-primary);
  }

  .chat-item.chosen {
    background: var(--surface-elevated);
    border-color: var(--border-subtle);
    color: var(--text-primary);
  }

  .sidebar-foot {
    margin-top: auto;
    font-size: 11px;
    color: var(--text-muted);
    letter-spacing: 0.08em;
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

  .idle-block {
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
</style>
