<script lang="ts">
  import './signout.css';
</script>

<form method="POST" action="/logout" class="signout-form">
  <button type="submit" class="ghost-button signout-button">
    <span class="signout-label">Sign out</span>
    <svg
      class="signout-icon"
      aria-hidden="true"
      focusable="false"
      viewBox="0 0 24 24"
      width="18"
      height="18"
      fill="none"
    >
      <path
        d="M10 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4M15 8l4 4-4 4M19 12H9"
        stroke="currentColor"
        stroke-width="1.6"
        stroke-linecap="round"
        stroke-linejoin="round"
      />
    </svg>
  </button>
</form>
