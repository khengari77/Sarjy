<script lang="ts">
  import type { VoiceOption } from '../api';
  import VoiceSettings from '../chat/VoiceSettings.svelte';
  import MemoryPanel from '../memory/MemoryPanel.svelte';
  import type { CatalogState } from '../voice/catalog';
  import RightDrawer from './RightDrawer.svelte';

  interface Props {
    arabic: boolean;
    conversationId: string | null;
    /** Changes when a turn finalises (memory capture) or the chat changes. */
    memoryRevision: string;
    catalog: CatalogState;
    selectedVoice: VoiceOption | null;
    sessionVoice: VoiceOption | null;
    live: boolean;
    captionsVisible: boolean;
    activeTab?: 'memories' | 'voice' | 'appearance' | 'context';
    onsearch: () => void;
    onloadmore: () => void;
    onremember: () => void;
    ontoggleCaptions: () => void;
  }

  let {
    arabic,
    conversationId,
    memoryRevision,
    catalog,
    selectedVoice,
    sessionVoice,
    live,
    captionsVisible,
    activeTab = $bindable('voice'),
    onsearch,
    onloadmore,
    onremember,
    ontoggleCaptions,
  }: Props = $props();
</script>

<RightDrawer {arabic} bind:activeTab>
  {#snippet memoryPanel(active)}
    <MemoryPanel {conversationId} {active} revision={memoryRevision} />
  {/snippet}
  {#snippet voicePanel()}
    <VoiceSettings
      {arabic}
      voices={catalog.voices}
      bind:voiceId={catalog.voiceId}
      bind:voiceSearch={catalog.search}
      nextVoicePage={catalog.nextPage}
      voiceLoading={catalog.loading}
      voiceError={catalog.error}
      voiceSaving={catalog.saving}
      settingsError={catalog.settingsError}
      {live}
      {selectedVoice}
      {sessionVoice}
      {captionsVisible}
      {onsearch}
      {onloadmore}
      {onremember}
      {ontoggleCaptions}
    />
  {/snippet}
</RightDrawer>
