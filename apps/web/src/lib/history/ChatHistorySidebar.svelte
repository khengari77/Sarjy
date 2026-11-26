<script lang="ts">
  import { tick } from 'svelte';
  import './history.css';
  import type { Conversation } from '../api';
  import { wordsFor } from '../chat/words';
  import DeleteConfirm from './DeleteConfirm.svelte';
  import { deleteFocusIndex, focusAfterDelete, focusConfirmAction } from './deleteFlow';
  import { filterConversations } from './chatSearch';

  interface Props {
    conversations: Conversation[];
    current: string | null;
    arabic: boolean;
    busy: boolean;
    loading: boolean;
    error: string;
    /** The conversation whose call is live, if any. */
    callConversation: string | null;
    onselect: (id: string) => void;
    oncreate: () => void;
    onretry: () => void;
    /** Returns an empty string on success, or a delete failure code. */
    ondelete: (id: string) => Promise<string>;
  }

  let {
    conversations,
    current,
    arabic,
    busy,
    loading,
    error,
    callConversation,
    onselect,
    oncreate,
    onretry,
    ondelete,
  }: Props = $props();

  let query = $state('');
  let menuOpenFor = $state<string | null>(null);
  let menuTrigger: HTMLButtonElement | null = null;
  let listElement = $state<HTMLElement | null>(null);
  let menuElement = $state<HTMLElement | null>(null);
  let confirmingDelete = $state<string | null>(null);
  let deletingId = $state<string | null>(null);
  let deleteError = $state('');

  const words = $derived(wordsFor(arabic));

  const filtered = $derived(filterConversations(conversations, query));
  const hasConversations = $derived(conversations.length > 0);
  const noMatches = $derived(hasConversations && filtered.length === 0);

  function select(id: string) {
    closeMenu();
    onselect(id);
  }

  function closeMenu(restoreFocus = false) {
    const trigger = menuTrigger;
    menuOpenFor = null;
    menuTrigger = null;
    menuElement = null;
    confirmingDelete = null;
    deleteError = '';
    if (restoreFocus) void tick().then(() => trigger?.focus());
  }

  function toggleMenu(id: string, event: MouseEvent) {
    if (deletingId) return;
    if (menuOpenFor === id) {
      closeMenu();
      return;
    }
    menuTrigger = event.currentTarget as HTMLButtonElement;
    menuOpenFor = id;
    confirmingDelete = null;
    deleteError = '';
  }

  function askDelete(id: string) {
    if (busy || deletingId || callConversation === id) return;
    confirmingDelete = id;
    deleteError = '';
    void tick().then(() => focusConfirmAction(menuElement));
  }

  function cancelDelete() {
    confirmingDelete = null;
    deleteError = '';
    void tick().then(() => menuTrigger?.focus());
  }

  async function confirmDelete() {
    if (!confirmingDelete || deletingId) return;
    const id = confirmingDelete;
    const focusIndex = deleteFocusIndex(
      filtered.map((chat) => chat.id),
      id,
    );
    deletingId = id;
    deleteError = '';
    const failure = await ondelete(id);
    deletingId = null;
    if (failure) {
      deleteError = failure === 'CONVERSATION_ACTIVE' ? words.deleteActive : words.deleteFailed;
      void tick().then(() => focusConfirmAction(menuElement));
      return;
    }
    closeMenu();
    void tick().then(() => focusAfterDelete(listElement, focusIndex));
  }

  // Dismiss on Escape (returning focus to the trigger) or on any outside
  // pointer interaction. Delete runs a confirmation step inside the menu.
  $effect(() => {
    if (!menuOpenFor) return;
    const onPointerDown = (event: PointerEvent) => {
      if (deletingId) return;
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (menuElement?.contains(target) || menuTrigger?.contains(target)) return;
      closeMenu();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || deletingId) return;
      event.preventDefault();
      event.stopPropagation();
      closeMenu(true);
    };
    document.addEventListener('pointerdown', onPointerDown, true);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true);
      document.removeEventListener('keydown', onKeyDown);
    };
  });

  const searchLabel = $derived(arabic ? 'البحث في المحادثات' : 'Search conversations');
</script>

<div class="sidebar-inner chat-history" dir={arabic ? 'rtl' : 'ltr'} bind:this={listElement}>
  <button class="new-chat" disabled={busy} onclick={oncreate}>
    <svg class="new-chat-icon" aria-hidden="true" viewBox="0 0 24 24" width="18" height="18" fill="none">
      <path d="M12 5.5v13M5.5 12h13" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
    </svg>
    <span>{arabic ? 'محادثة جديدة' : 'New conversation'}</span>
  </button>

  <label class="search">
    <span class="sr-only">{searchLabel}</span>
    <svg class="search-icon" aria-hidden="true" viewBox="0 0 24 24" width="17" height="17" fill="none">
      <circle cx="10.8" cy="10.8" r="6.3" stroke="currentColor" stroke-width="1.7" />
      <path d="m15.6 15.6 4.1 4.1" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" />
    </svg>
    <input type="search" bind:value={query} placeholder={searchLabel} aria-label={searchLabel} />
    {#if query}
      <button
        type="button"
        class="clear"
        onclick={() => (query = '')}
        aria-label={arabic ? 'مسح البحث' : 'Clear search'}
      >
        ✕
      </button>
    {/if}
  </label>

  <div class="history-head">
    <p class="sidebar-label">{arabic ? 'الأخيرة' : 'RECENT'}</p>
  </div>

  {#if error}
    <div class="panel-error" role="alert" data-code={error}>
      <p>{arabic ? 'تعذّر تحميل المحادثات.' : 'Could not load conversations.'}</p>
      <button class="ghost-button" onclick={onretry}>{arabic ? 'إعادة المحاولة' : 'Retry'}</button>
    </div>
  {:else if loading}
    <div class="skeleton" aria-busy="true" aria-label={arabic ? 'جارٍ تحميل المحادثات' : 'Loading conversations'}>
      <span></span><span></span><span></span>
    </div>
  {:else if !hasConversations}
    <div class="empty">
      <p class="empty-title">{arabic ? 'لا توجد محادثات بعد' : 'No conversations yet'}</p>
      <p class="empty-copy">{arabic ? 'ستظهر محادثاتك هنا.' : 'Your chats will appear here.'}</p>
      <div class="empty-hint">
        <p class="empty-hint-title">{arabic ? 'ابدأ بصوتك' : 'Start with your voice'}</p>
        <p class="empty-hint-copy">{arabic ? 'أو استخدم مربّع الرسائل.' : 'Or use the message box.'}</p>
      </div>
    </div>
  {:else if noMatches}
    <div class="empty">
      <p class="empty-title">{arabic ? 'لا توجد نتائج مطابقة.' : 'No matching conversations.'}</p>
      <button class="ghost-button" onclick={() => (query = '')}>{arabic ? 'مسح البحث' : 'Clear search'}</button>
    </div>
  {:else}
    <nav class="chat-list" aria-label={arabic ? 'قائمة المحادثات' : 'Conversation list'}>
      {#each filtered as chat (chat.id)}
        <div class="chat-row" class:chosen={current === chat.id}>
          <button
            class="chat-item"
            class:chosen={current === chat.id}
            aria-current={current === chat.id ? 'page' : undefined}
            disabled={busy}
            onclick={() => select(chat.id)}
          >
            <svg class="chat-glyph" aria-hidden="true" viewBox="0 0 24 24" width="17" height="17" fill="none">
              <path
                d="M20.4 11.6a7.6 7.6 0 0 1-7.6 7.6H6.2L3.4 21.5v-9.9a7.6 7.6 0 0 1 7.6-7.6h1.8a7.6 7.6 0 0 1 7.6 7.6Z"
                stroke="currentColor"
                stroke-width="1.6"
                stroke-linejoin="round"
              />
            </svg>
            <span class="title" dir="auto">{chat.title}</span>
          </button>
          <button
            class="menu-toggle"
            type="button"
            aria-haspopup="menu"
            aria-expanded={menuOpenFor === chat.id}
            aria-label={(arabic ? 'خيارات المحادثة ' : 'Conversation actions for ') + chat.title}
            onclick={(event) => toggleMenu(chat.id, event)}
          >
            ⋯
          </button>
          {#if callConversation === chat.id}
            <p class="sr-only" id={`delete-active-${chat.id}`}>{words.deleteActive}</p>
          {/if}
          {#if menuOpenFor === chat.id}
            <div class="menu" role="menu" bind:this={menuElement}>
              <button role="menuitem" disabled>{words.rename}</button>
              {#if confirmingDelete === chat.id}
                <DeleteConfirm
                  {words}
                  {busy}
                  deleting={deletingId === chat.id}
                  error={deleteError}
                  oncancel={cancelDelete}
                  onconfirm={() => void confirmDelete()}
                />
              {:else}
                <button
                  role="menuitem"
                  disabled={busy || callConversation === chat.id}
                  aria-describedby={callConversation === chat.id ? `delete-active-${chat.id}` : undefined}
                  onclick={() => askDelete(chat.id)}
                >
                  {words.delete}
                </button>
                <p>{words.renameNeedsBackend}</p>
              {/if}
            </div>
          {/if}
        </div>
      {/each}
    </nav>
  {/if}

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
</div>
