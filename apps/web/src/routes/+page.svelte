<script lang="ts">
  import { onMount } from 'svelte';
  import {
    request,
    type Conversation,
    type Turn,
    type SessionConfig,
    type VoiceOption,
    type VoiceCatalog,
  } from '../lib/api';
  import { LiveKitVoiceRuntime } from '../lib/audio/voice-runtime';
  import CallControls from '../lib/chat/CallControls.svelte';
  import Composer from '../lib/chat/Composer.svelte';
  import ConversationList from '../lib/chat/ConversationList.svelte';
  import Transcript from '../lib/chat/Transcript.svelte';
  import VoiceSettings from '../lib/chat/VoiceSettings.svelte';
  import {
    bootstrapConversations,
    createConversation,
    endVoiceSession,
    fetchConversation,
    mergeVoices,
    startVoiceSession,
    type ToolCall,
  } from '../lib/chat/session';
  import { wordsFor } from '../lib/chat/words';
  import '../lib/chat/chat.css';

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
  let toolCalls = $state<ToolCall[]>([]);
  let current = $state<string | null>(null);
  let turns = $state<Turn[]>([]);
  let draft = $state('');
  let status = $state('idle');
  let error = $state('');
  let busy = $state(false);
  let muted = $state(true);
  let audioBlocked = $state(false);
  let live = $state<SessionConfig | null>(null);
  let runtime: LiveKitVoiceRuntime;
  const words = $derived(wordsFor(arabic));

  async function loadVoices(more = false) {
    if (voiceLoading) return;
    voiceLoading = true;
    voiceError = '';
    try {
      const search = more ? loadedVoiceSearch : voiceSearch;
      const query = new URLSearchParams({ search });
      if (more && nextVoicePage) query.set('next_page_token', nextVoicePage);
      const result = await request<VoiceCatalog>('voices?' + query);
      voices = more ? mergeVoices(voices, result.voices) : result.voices;
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
      const history = await fetchConversation(id);
      if (current === id) {
        turns = history.turns;
        toolCalls = history.toolCalls;
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
    live = null;
    muted = true;
    await runtime?.disconnect();
    await endVoiceSession(session);
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
      const chat = await createConversation();
      conversations = [chat, ...conversations];
      await select(chat.id);
    });
  }

  async function connect(microphone: boolean) {
    if (!current) throw new Error('CONVERSATION_REQUIRED');
    if (!live) {
      const requestedVoiceId = voiceId;
      const requestedVoiceName = voices.find((voice) => voice.id === requestedVoiceId)?.name || requestedVoiceId;
      const config = await startVoiceSession(current, arabic ? 'ar' : 'en', requestedVoiceId);
      try {
        await runtime.connect(config);
        live = config;
        activeVoiceName = requestedVoiceName;
      } catch (e) {
        await runtime.disconnect();
        await request('voice/sessions/' + config.id + '/end', {});
        throw e;
      }
    }
    if (microphone) {
      await runtime.startMicrophone();
      muted = false;
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

  function toggleLanguage() {
    arabic = !arabic;
    localStorage.setItem('sarjy-language', arabic ? 'ar' : 'en');
  }

  async function toggleMute() {
    if (muted) await runtime.startMicrophone();
    else await runtime.stopMicrophone();
    muted = !muted;
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
      conversations = await bootstrapConversations();
      await select(conversations[0].id);
    });
    const poll = setInterval(() => {
      if (live && Date.parse(live.expires_at) <= Date.now()) void run(end);
      else if (live) void refresh().catch(() => {});
    }, 2500);
    return () => {
      clearInterval(poll);
      unsubscribe();
      void runtime.disconnect();
      if (live) void endVoiceSession(live).catch(() => {});
    };
  });
</script>

<svelte:head>
  <title>Sarjy — Voice Context Studio</title>
  <meta name="description" content="A bilingual voice assistant with transparent context." />
</svelte:head>

<div class="shell" dir={arabic ? 'rtl' : 'ltr'}>
  <ConversationList
    {conversations}
    {current}
    {busy}
    labelNew={words.new}
    oncreate={() => void create()}
    onselect={(id) => void run(() => select(id))}
  />
  <main>
    <header>
      <span>● {status}</span>
      <button onclick={toggleLanguage}>{arabic ? 'English' : 'العربية'}</button>
    </header>
    {#if error}<p role="alert">{error}</p>{/if}
    {#if audioBlocked}
      <button
        onclick={() =>
          run(async () => {
            await runtime.resumeAudio();
            audioBlocked = false;
          })}>Enable audio / تشغيل الصوت</button
      >
    {/if}
    <Transcript {turns} {toolCalls} title={words.title} intro={words.intro} />
    <VoiceSettings
      {arabic}
      {voices}
      bind:voiceId
      bind:voiceSearch
      {nextVoicePage}
      {voiceLoading}
      {voiceError}
      {live}
      {activeVoiceName}
      onsearch={() => void loadVoices()}
      onloadmore={() => void loadVoices(true)}
      onremember={rememberVoice}
    />
    <CallControls
      {live}
      {busy}
      {muted}
      canConnect={Boolean(current)}
      {words}
      onend={() => void run(end)}
      ontoggle={() => void run(toggleMute)}
      oninterrupt={() => void run(() => runtime.interrupt())}
      onconnect={() => void run(() => connect(true))}
    />
    <Composer
      bind:draft
      placeholder={words.placeholder}
      sendLabel={words.send}
      disabled={busy || !current}
      onsend={() => void send()}
    />
    <small><a href="https://open-meteo.com/">Weather data: Open-Meteo</a></small>
  </main>
</div>
