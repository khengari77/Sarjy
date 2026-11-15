<script lang="ts">
  import type { Turn, VoiceOption } from '../api';
  import TranscriptPanel from '../chat/TranscriptPanel.svelte';
  import type { ProvisionalTranscript } from '../voice/connection';
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
    speakingSpeed: number;
    captionsVisible: boolean;
    turns: Turn[];
    provisional?: ProvisionalTranscript | null;
    tools: { id: string; name: string; result: unknown }[];
    activeTab?: 'memories' | 'controls' | 'context' | 'transcript';
    onsearch: () => void;
    onloadmore: () => void;
    onremember: () => void;
    onspeed: (speed: number) => Promise<boolean>;
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
    speakingSpeed,
    captionsVisible,
    turns,
    provisional = null,
    tools,
    activeTab = $bindable('controls'),
    onsearch,
    onloadmore,
    onremember,
    onspeed,
    ontoggleCaptions,
  }: Props = $props();
</script>

<RightDrawer {arabic} bind:activeTab>
  {#snippet memoryPanel(active)}
    <MemoryPanel {conversationId} {active} revision={memoryRevision} />
  {/snippet}
  {#snippet transcriptPanel()}
    <TranscriptPanel {turns} {provisional} {tools} {arabic} />
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
      {speakingSpeed}
      {captionsVisible}
      {onsearch}
      {onloadmore}
      {onremember}
      {onspeed}
      {ontoggleCaptions}
    />
  {/snippet}
</RightDrawer>
