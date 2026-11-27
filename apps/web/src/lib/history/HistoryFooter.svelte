<script lang="ts">
  let { arabic }: { arabic: boolean } = $props();
</script>

<footer class="sidebar-foot">
  <div class="workspace-row">
    <span class="workspace-avatar" aria-hidden="true">S</span>
    <span>{arabic ? 'مساحة عملك' : 'Your workspace'}</span>
  </div>
  <div class="sidebar-credit">
    <a href="https://open-meteo.com/" rel="noreferrer">Weather data: Open-Meteo</a>
    <span>Voice Context Studio</span>
  </div>
</footer>
