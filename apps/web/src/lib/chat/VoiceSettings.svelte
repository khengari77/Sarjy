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
    activeVoiceName,
    onsearch,
    onloadmore,
    onremember,
  }: {
    arabic: boolean;
    voices: VoiceOption[];
    voiceId?: string;
    voiceSearch?: string;
    nextVoicePage: string | null;
    voiceLoading: boolean;
    voiceError: string;
    live: SessionConfig | null;
    activeVoiceName: string;
    onsearch: () => void;
    onloadmore: () => void;
    onremember: () => void;
  } = $props();
</script>

<fieldset class="voice-settings">
  <legend>{arabic ? 'صوت المساعد' : 'Assistant voice'}</legend>
  <form
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
    <button type="submit" disabled={voiceLoading}>{arabic ? 'تحميل الأصوات / بحث' : 'Load voices / Search'}</button>
  </form>
  <label for="assistant-voice">{arabic ? 'اختر الصوت' : 'Choose voice'}</label>
  <select id="assistant-voice" bind:value={voiceId} onchange={onremember}>
    <option value="">{arabic ? 'اختر صوتًا قبل بدء المكالمة' : 'Select a voice before calling'}</option>
    {#if voiceId && !voices.some((voice) => voice.id === voiceId)}
      <option value={voiceId}>{arabic ? 'الصوت المحفوظ (أعد تحميل القائمة)' : 'Saved voice (reload list)'}</option>
    {/if}
    {#each voices as voice (voice.id)}
      <option value={voice.id}>{voice.name}{voice.languages?.length ? ' · ' + voice.languages.join(', ') : ''}</option>
    {/each}
  </select>
  {#if nextVoicePage}<button disabled={voiceLoading} onclick={onloadmore}>{arabic ? 'المزيد' : 'Load more'}</button
    >{/if}
  {#if voiceError}
    <p class="voice-error">{arabic ? 'تعذّر تحميل الأصوات' : 'Could not load voices'}: {voiceError}</p>
  {/if}
  {#if voices.length === 0 && !voiceLoading && !voiceError}
    <small
      >{arabic
        ? 'حمّل الأصوات المتاحة لاختيار صوت مناسب للعربية.'
        : 'Load available voices to choose an English voice.'}</small
    >
  {/if}
  <small
    >{arabic
      ? 'التغييرات تسري على المكالمة التالية. تسميات اللغة لا تضمن جودة النطق.'
      : 'Changes apply to the next call. Language labels do not guarantee pronunciation quality.'}</small
  >
  {#if live}<small>{arabic ? 'صوت المكالمة الحالية' : 'Current call voice'}: {activeVoiceName}</small>{/if}
</fieldset>
