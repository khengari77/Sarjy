<script lang="ts">
  import type { VoiceOption } from '../api';
  import { friendlyVoiceError } from '../audio/livekitAdapter';
  import { voiceStatusText, type VoiceStatus } from './voice-state';

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
    confirmedVoice,
    status,
    captionsVisible,
    onsearch,
    onloadmore,
    onremember,
    ontoggleCaptions,
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
    confirmedVoice: VoiceOption | null;
    status: VoiceStatus;
    captionsVisible: boolean;
    onsearch: () => void;
    onloadmore: () => void;
    onremember: () => void;
    ontoggleCaptions: () => void;
  } = $props();

  const statusText = $derived(
    voiceStatusText({ selected: selectedVoice, session: sessionVoice, confirmed: confirmedVoice, live }, arabic),
  );
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
      {#if !voiceId}
        <option value="">{arabic ? 'الصوت الافتراضي من الخادم' : 'Server default voice'}</option>
      {/if}
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
        {arabic
          ? 'سيُستخدم الصوت الافتراضي. حمّل الأصوات لاختيار صوت آخر.'
          : 'The default voice will be used. Load voices to choose a different one.'}
      </small>
    {/if}
    <small class="muted-text">
      {arabic
        ? 'تسري تغييرات الصوت على المكالمة التالية. تسميات اللغة لا تضمن جودة النطق.'
        : 'Voice changes apply to the next call. Language labels do not guarantee pronunciation quality.'}
    </small>
  </section>

  <section class="panel-block">
    <h2>{arabic ? 'التجربة' : 'Experience'}</h2>
    <label class="toggle-row">
      <input type="checkbox" checked={captionsVisible} onchange={ontoggleCaptions} />
      <span>{arabic ? 'إظهار الترجمة' : 'Show captions'}</span>
    </label>
    {#if live}
      <small class="muted-text">
        {arabic
          ? 'تغيير الصوت أثناء المكالمة يسري على المكالمة التالية.'
          : 'Changing the voice mid-call applies to the next call.'}
      </small>
    {/if}
  </section>
</div>
