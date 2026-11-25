<script lang="ts">
  import type { VoiceOption } from '../api';
  import { friendlyVoiceError } from '../audio/livekitAdapter';
  import type { MemoryMode } from '../commands/commandStore';
  import { wordsFor } from './words';
  import { voiceStatus, voiceStatusText } from './voice-state';

  let {
    arabic,
    voices,
    voiceId = $bindable(''),
    voiceSearch = $bindable(''),
    nextVoicePage,
    voiceLoading,
    voiceError,
    voiceSaving,
    settingsError,
    live,
    selectedVoice,
    sessionVoice,
    confirmedVoice = null,
    speakingSpeed,
    captionsVisible,
    memoryMode,
    memorySaving,
    memoryError,
    onsearch,
    onloadmore,
    onremember,
    onspeed,
    ontoggleCaptions,
    onmemorymode,
  }: {
    arabic: boolean;
    voices: VoiceOption[];
    voiceId?: string;
    voiceSearch?: string;
    nextVoicePage: string | null;
    voiceLoading: boolean;
    voiceError: string;
    voiceSaving: boolean;
    settingsError: string;
    live: boolean;
    selectedVoice: VoiceOption | null;
    sessionVoice: VoiceOption | null;
    confirmedVoice?: VoiceOption | null;
    speakingSpeed: number;
    captionsVisible: boolean;
    memoryMode: MemoryMode;
    memorySaving: boolean;
    memoryError: string;
    onsearch: () => void;
    onloadmore: () => void;
    onremember: () => void;
    onspeed: (speed: number) => Promise<boolean>;
    ontoggleCaptions: () => void;
    onmemorymode: (mode: MemoryMode) => void;
  } = $props();

  const words = $derived(wordsFor(arabic));
  const MEMORY_MODES = $derived<{ mode: MemoryMode; label: string }[]>([
    { mode: 'off', label: words.memoryModeOff },
    { mode: 'ask', label: words.memoryModeAsk },
    { mode: 'auto', label: words.memoryModeAuto },
  ]);

  // Mirrors the server contract (SPEAKING_SPEED_MIN/MAX/STEP). The server still
  // validates every write; this only decides which options the picker offers.
  const SPEEDS = [0.8, 0.9, 1, 1.1, 1.2];

  const status = $derived(
    voiceStatus({ selected: selectedVoice, session: sessionVoice, confirmed: confirmedVoice, live }),
  );
  const statusText = $derived(
    voiceStatusText({ selected: selectedVoice, session: sessionVoice, confirmed: confirmedVoice, live }, arabic),
  );

  let speedSaving = $state(false);
  let speedError = $state('');

  async function changeSpeed(event: Event) {
    const speed = Number((event.currentTarget as HTMLSelectElement).value);
    speedSaving = true;
    speedError = '';
    try {
      if (!(await onspeed(speed))) {
        speedError = arabic
          ? 'تعذّر حفظ سرعة النطق. لم يتغيّر شيء.'
          : 'Speaking speed could not be saved. Nothing changed.';
      }
    } finally {
      speedSaving = false;
    }
  }
</script>

<div class="sidebar-inner" dir={arabic ? 'rtl' : 'ltr'}>
  <section class="panel-block">
    <h2>{arabic ? 'الصوت' : 'Voice'}</h2>
    <p class="voice-status" data-voice-status={status}>{statusText}</p>
    <form
      class="voice-search"
      onsubmit={(event) => {
        event.preventDefault();
        onsearch();
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
    <select id="assistant-voice" bind:value={voiceId} disabled={voiceSaving} onchange={onremember}>
      <option value="">{arabic ? 'الصوت الافتراضي من الخادم' : 'Server default voice'}</option>
      {#if voiceId && !voices.some((voice) => voice.id === voiceId)}
        <option value={voiceId}>{arabic ? 'الصوت المحفوظ (أعد تحميل القائمة)' : 'Saved voice (reload list)'}</option>
      {/if}
      {#each voices as voice (voice.id)}
        <option value={voice.id}>{voice.name}{voice.languages?.length ? ' · ' + voice.languages.join(', ') : ''}</option
        >
      {/each}
    </select>
    {#if nextVoicePage}
      <button class="ghost-button" disabled={voiceLoading} onclick={onloadmore}>
        {arabic ? 'المزيد' : 'Load more'}
      </button>
    {/if}
    {#if voiceSaving}
      <small class="muted-text">{arabic ? 'جارٍ حفظ اختيار الصوت…' : 'Saving voice selection…'}</small>
    {/if}
    {#if voiceError}
      <p class="voice-error" role="status">{friendlyVoiceError(voiceError, arabic)}</p>
    {/if}
    {#if settingsError}
      <p class="voice-error" role="alert">{friendlyVoiceError(settingsError, arabic)}</p>
    {/if}
    {#if voices.length === 0 && !voiceLoading && !voiceError}
      <small class="muted-text">
        {#if voiceId}
          {arabic
            ? 'الصوت المحفوظ ليس في القائمة المحمّلة. حمّل الأصوات لتأكيد الاسم.'
            : 'The saved voice is not in the loaded list. Load voices to confirm its name.'}
        {:else}
          {arabic
            ? 'سيُستخدم الصوت الافتراضي. حمّل الأصوات لاختيار صوت آخر.'
            : 'The default voice will be used. Load voices to choose a different one.'}
        {/if}
      </small>
    {/if}
    <small class="muted-text">
      {arabic
        ? 'تسري تغييرات الصوت على المكالمة التالية. تسميات اللغة لا تضمن جودة النطق.'
        : 'Voice changes apply to the next call. Language labels do not guarantee pronunciation quality.'}
    </small>
  </section>

  <section class="panel-block">
    <h2>{arabic ? 'سرعة النطق' : 'Speed'}</h2>
    <label class="field-label" for="assistant-speed">{arabic ? 'سرعة المتحدث' : 'Speaking speed'}</label>
    <select
      id="assistant-speed"
      value={speakingSpeed}
      disabled={speedSaving}
      onchange={changeSpeed}
      aria-describedby="assistant-speed-note"
    >
      {#each SPEEDS as speed (speed)}
        <option value={speed}>{speed.toFixed(1)}×</option>
      {/each}
    </select>
    <small class="muted-text" id="assistant-speed-note">
      {arabic ? 'تسري السرعة على الدور التالي في المكالمة.' : 'Speed applies to the next turn in the call.'}
    </small>
    {#if speedSaving}
      <small class="muted-text">{arabic ? 'جارٍ حفظ السرعة…' : 'Saving speed…'}</small>
    {/if}
    {#if speedError}
      <p class="voice-error" role="alert">{speedError}</p>
    {/if}
  </section>

  <section class="panel-block">
    <h2>{words.memoryMode}</h2>
    <span class="field-label" id="memory-mode-label">
      {arabic ? 'حفظ الذاكرة' : 'Memory capture'}
    </span>
    <div class="segmented-control" role="group" aria-labelledby="memory-mode-label">
      {#each MEMORY_MODES as option (option.mode)}
        <button
          type="button"
          class="segmented-button"
          data-active={memoryMode === option.mode}
          aria-pressed={memoryMode === option.mode}
          disabled={memorySaving}
          onclick={() => onmemorymode(option.mode)}
        >
          {option.label}
        </button>
      {/each}
    </div>
    <small class="muted-text">{words.memoryModeNote}</small>
    {#if memoryError}
      <p class="voice-error" role="alert">{memoryError}</p>
    {/if}
  </section>

  <section class="panel-block">
    <h2>{arabic ? 'العرض' : 'Display'}</h2>
    <label class="toggle-row">
      <input type="checkbox" checked={captionsVisible} onchange={ontoggleCaptions} />
      <span>{arabic ? 'إظهار الترجمة' : 'Show captions'}</span>
    </label>
    <small class="muted-text">
      {arabic
        ? 'تظهر الترجمة المباشرة على المسرح؛ لا تغيّر الصوت ولا ما يسمعه المساعد.'
        : 'Live captions appear on the stage; they do not change the voice or what the assistant hears.'}
    </small>
  </section>
</div>
