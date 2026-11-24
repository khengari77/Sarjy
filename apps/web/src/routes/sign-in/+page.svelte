<script lang="ts">
  import AccountForm from '../../lib/auth/AccountForm.svelte';
  import type { ActionData, PageData } from './$types';

  let { data, form }: { data: PageData; form: ActionData } = $props();
</script>

<AccountForm
  action="sign-in"
  next={form?.next ?? data.next}
  email={form?.email ?? ''}
  message={form?.message ?? data.message}
  providers={data.providers}
/>
