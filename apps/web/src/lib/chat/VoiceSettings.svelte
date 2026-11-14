<script lang="ts">
  import type { SessionConfig, VoiceOption } from '../api';

  let {
    arabic,
    voices,
    voiceId = $bindable(''),
    voiceSearch = $bindable(''),
    nextVoicePage,
    voiceLoading,
    voiceError,
    live,
    confirmedVoiceName,
    switchStatus,
    switchError,
    captionsVisible,
    onsearch,
    onloadmore,
    onvoicechange,
    ontoggleCaptions,
  }: {
    arabic: boolean;
    voices: VoiceOption[];
    voiceId?: string;
    voiceSearch?: string;
    nextVoicePage: string | null;
    voiceLoading: boolean;
    voiceError: string;
    live: SessionConfig | null;
    confirmedVoiceName: string;
    switchStatus: 'idle' | 'pending' | 'failed';
    switchError: string;
    captionsVisible: boolean;
    onsearch: () => void;
    onloadmore: () => void;
    onvoicechange: () => void;
    ontoggleCaptions: () => void;
  } = $props();
</script>

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
    <select id="assistant-voice" bind:value={voiceId} onchange={onvoicechange}>
      <option value="">{arabic ? 'اختر صوتًا قبل بدء المكالمة' : 'Select a voice before calling'}</option>
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
        : 'Changes apply to the next call. During a call, they apply to the next reply once confirmed.'}
    </small>
    {#if live}
      <p class="voice-active" aria-live="polite">
        {#if switchStatus === 'pending'}
          <span>{arabic ? 'جارٍ تحديث الصوت…' : 'Updating voice…'}</span>
          <small class="muted-text">
            {arabic ? 'يسري على الردّ التالي.' : 'Applies to the next reply.'}
          </small>
        {:else}
          <span>{arabic ? 'الصوت النشط' : 'Active voice'}: {confirmedVoiceName}</span>
        {/if}
      </p>
      {#if switchStatus === 'failed' && switchError}
        <p class="voice-error" role="alert">{switchError}</p>
      {/if}
    {/if}
  </section>

  <section class="panel-block">
    <h2>{arabic ? 'التجربة' : 'Experience'}</h2>
    <label class="toggle-row">
      <input type="checkbox" checked={captionsVisible} onchange={ontoggleCaptions} />
      <span>{arabic ? 'إظهار الترجمة' : 'Show captions'}</span>
    </label>
    <p class="muted-text">
      {arabic ? 'سيُضاف التحكم بسرعة الكلام هنا.' : 'Speaking-speed controls will live here.'}
    </p>
  </section>
</div>
