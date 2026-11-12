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
  import { LiveKitVoiceRuntime } from '../lib/audio/voice-runtime';
  import VoiceOrb from '../lib/orb/VoiceOrb.svelte';
  import { createEnvelopeMeter, type EnvelopeMeter } from '../lib/orb/audioEnvelope';
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
  let status = $state('idle');
  let error = $state('');
  let busy = $state(false);
  let muted = $state(true);
  let audioBlocked = $state(false);
  let live = $state<SessionConfig | null>(null);
  let micLevel = $state(0);
  let micMeter: EnvelopeMeter | null = null;
  let meterTimer: ReturnType<typeof setInterval> | null = null;
  let runtime: LiveKitVoiceRuntime;

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

  const PHASE_BY_STATUS: Record<string, VoicePhase> = {
    idle: 'idle',
    connecting: 'connecting',
    starting: 'connecting',
    reconnecting: 'connecting',
    connected: 'listening',
    listening: 'listening',
    thinking: 'thinking',
    speaking: 'speaking',
    disconnected: 'disconnected'
  };

  const presentation = $derived(
    deriveOrbPresentation({
      ...INITIAL_STATE,
      phase: PHASE_BY_STATUS[status] ?? 'idle',
      overlay: muted && status !== 'idle' ? 'muted' : 'none',
      micActive: !muted,
      sessionId: live?.id ?? null,
      connectionReady: live !== null
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

  async function run(work: () => Promise<void>) {
    busy = true;
    error = '';
    try {
      await work();
    } catch (e) {
      error = e instanceof Error ? e.message : 'UNAVAILABLE';
    } finally {
      busy = false;
    }
  }

  async function end() {
    const session = live;
    teardownMeters();
    live = null;
    muted = true;
    await runtime?.disconnect();
    if (session) await request('voice/sessions/' + session.id + '/end', {});
    status = 'idle';
    await refresh();
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
    if (!current) throw new Error('CONVERSATION_REQUIRED');
    if (!live) {
      const requestedVoiceId = voiceId;
      const requestedVoiceName = voices.find((voice) => voice.id === requestedVoiceId)?.name || requestedVoiceId;
      const config = await request<SessionConfig>('voice/sessions', {
        conversation_id: current,
        language: arabic ? 'ar' : 'en',
        voice_id: requestedVoiceId || null,
        response_preference: 'automatic'
      } satisfies StartVoice);
      try {
        status = 'connecting';
        await runtime.connect(config);
        live = config;
        activeVoiceName = requestedVoiceName;
      } catch (e) {
        await runtime.disconnect();
        await request('voice/sessions/' + config.id + '/end', {});
        status = 'disconnected';
        throw e;
      }
    }
    if (microphone) {
      await runtime.startMicrophone();
      muted = false;
      setupMeters();
    }
  }

  async function send() {
    if (!draft.trim()) return;
    await run(async () => {
      await connect(false);
      await runtime.sendText(draft.trim());
      draft = '';
    });
  }

  async function toggleMute() {
    await run(async () => {
      if (muted) await runtime.startMicrophone();
      else await runtime.stopMicrophone();
      muted = !muted;
      setupMeters();
    });
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
    const stream = runtime?.microphoneStream();
    if (!stream) return;
    micMeter = createEnvelopeMeter(stream);
    meterTimer = setInterval(() => {
      micLevel = micMeter?.level() ?? 0;
    }, 80);
  }

  onMount(() => {
    arabic = localStorage.getItem('sarjy-language') === 'ar';
    voiceId = localStorage.getItem('sarjy-voice-id') || '';
    runtime = new LiveKitVoiceRuntime();
    const unsubscribe = runtime.subscribe((event) => {
      if (event.type === 'status') {
        status = event.text;
        if (event.text === 'disconnected' && live) void run(end);
      }
      if (event.type === 'error') error = event.text;
      if (event.type === 'audio-blocked') audioBlocked = true;
      if (event.type === 'transcript') void refresh().catch(() => {});
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
    const poll = setInterval(() => {
      if (live && Date.parse(live.expires_at) <= Date.now()) void run(end);
      else if (live) void refresh().catch(() => {});
    }, 2500);
    return () => {
      teardownMeters();
      clearInterval(poll);
      unsubscribe();
      void runtime.disconnect();
      if (live) void request('voice/sessions/' + live.id + '/end', {}).catch(() => {});
    };
  });

  function toggleLanguage() {
    arabic = !arabic;
    localStorage.setItem('sarjy-language', arabic ? 'ar' : 'en');
  }

  const statusIsError = $derived(error !== '');
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

      {#if error}
        <p class="alert" role="alert">{error}</p>
      {/if}
      {#if audioBlocked}
        <button
          class="primary-button"
          onclick={() =>
            run(async () => {
              await runtime.resumeAudio();
              audioBlocked = false;
            })}
        >
          {arabic ? 'تشغيل الصوت' : 'Enable audio'}
        </button>
      {/if}

      {#if turns.length}
        <div class="turns">
          {#each turns as turn}
            <article class="turn" class:assistant={turn.role === 'assistant'} dir="auto">
              <small>{turn.role} · {turn.status}</small>
              <p>{turn.text}</p>
            </article>
          {/each}
        </div>
      {:else}
        <div class="idle-block">
          <VoiceOrb
            {presentation}
            {micLevel}
            label={arabic ? presentation.ariaLabel.ar : presentation.ariaLabel.en}
            disabled={busy}
            onactivate={() => {
              if (live) {
                if (!muted) void runtime.interrupt();
                return;
              }
              void run(() => connect(true));
            }}
          />
          <h1>{words.title}</h1>
          <p class="intro">{words.intro}</p>
        </div>
      {/if}

      {#each toolCalls as tool}
        <details class="tool-trace">
          <summary>{tool.name} · Open-Meteo</summary>
          <pre dir="ltr">{JSON.stringify(tool.result, null, 2)}</pre>
        </details>
      {/each}
    </section>

    <div class="controls">
      {#if live}
        <button class="primary-button" disabled={busy} onclick={() => run(end)}>{words.end}</button>
        <button class="ghost-button" disabled={busy} onclick={toggleMute}>{muted ? words.unmute : words.mute}</button>
        <button class="ghost-button" disabled={busy} onclick={() => run(() => runtime.interrupt())}>
          {arabic ? 'إيقاف' : 'Stop'}
        </button>
      {:else}
        <button class="primary-button" disabled={busy || !current} onclick={() => run(() => connect(true))}>
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
        <p class="muted-text">
          {arabic ? 'سيُضاف التحكم بالترجمة وسرعة الكلام هنا.' : 'Captions and speaking-speed controls will live here.'}
        </p>
      </section>
    </div>
  {/snippet}
</AppShell>

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

  .turns {
    width: 100%;
    display: flex;
    flex-direction: column;
    gap: 12px;
    overflow-y: auto;
    padding: 8px 2px;
  }

  .turn {
    text-align: start;
    background: var(--surface-elevated);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-panel);
    padding: 14px 16px;
  }

  .turn.assistant {
    background: color-mix(in srgb, var(--surface-elevated) 70%, var(--bg-app));
  }

  .turn small {
    color: var(--text-muted);
    font-size: 11px;
    letter-spacing: 0.04em;
  }

  .turn p {
    margin: 6px 0 0;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }

  .tool-trace {
    width: 100%;
    text-align: start;
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-control);
    padding: 10px 12px;
    color: var(--text-muted);
  }

  .tool-trace pre {
    overflow-x: auto;
    font-size: 12px;
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
