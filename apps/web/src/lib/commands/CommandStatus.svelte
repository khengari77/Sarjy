<script lang="ts">
  import type { CommandSession } from './commandSession.svelte';

  let { session }: { session: CommandSession } = $props();
</script>

<p
  class="sr-only"
  data-command-kind={session.state.last?.kind ?? ''}
  data-command-trace={session.state.trace.join(',')}
  data-settings-revision={session.state.settings.revision}
  data-captions-visible={session.state.settings.captions_visible}
  data-speaking-speed={session.state.settings.speaking_speed}
>
  {session.state.last?.spoken ?? ''}
</p>
