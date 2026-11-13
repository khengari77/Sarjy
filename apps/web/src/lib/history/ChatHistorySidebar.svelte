<script lang="ts">
  import { tick } from 'svelte';
  import './history.css';
  import type { Conversation } from '../api';
  import { filterConversations } from './chatSearch';

  interface Props {
    conversations: Conversation[];
    current: string | null;
    arabic: boolean;
    busy: boolean;
    loading: boolean;
    error: string;
    onselect: (id: string) => void;
    oncreate: () => void;
    onretry: () => void;
  }

  let { conversations, current, arabic, busy, loading, error, onselect, oncreate, onretry }: Props = $props();

  let query = $state('');
  let menuOpenFor = $state<string | null>(null);
  let menuTrigger: HTMLButtonElement | null = null;
  let menuElement = $state<HTMLElement | null>(null);

  const filtered = $derived(filterConversations(conversations, query));
  const hasConversations = $derived(conversations.length > 0);
  const noMatches = $derived(hasConversations && filtered.length === 0);

  function select(id: string) {
    menuOpenFor = null;
    menuTrigger = null;
    onselect(id);
  }

  function closeMenu(restoreFocus = false) {
    const trigger = menuTrigger;
    menuOpenFor = null;
    menuTrigger = null;
    menuElement = null;
    if (restoreFocus) void tick().then(() => trigger?.focus());
  }

  function toggleMenu(id: string, event: MouseEvent) {
    if (menuOpenFor === id) {
      closeMenu();
      return;
    }
    menuTrigger = event.currentTarget as HTMLButtonElement;
    menuOpenFor = id;
  }

  // Dismiss on Escape (returning focus to the trigger) or on any outside
  // pointer interaction. The menu holds only disabled items today, but the
  // dismissal path must stay reliable once actions are enabled.
  $effect(() => {
    if (!menuOpenFor) return;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (menuElement?.contains(target) || menuTrigger?.contains(target)) return;
      closeMenu();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
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
</script>

<div class="sidebar-inner chat-history" dir={arabic ? 'rtl' : 'ltr'} aria-busy={loading}>
  <button class="primary-button new-chat" disabled={busy} onclick={oncreate}>
    {arabic ? 'محادثة جديدة' : 'New conversation'} +
  </button>

  <div class="history-head">
    <p class="sidebar-label">{arabic ? 'المحادثات' : 'Conversations'}</p>
    {#if hasConversations}
      <span class="count" aria-live="polite">
        {filtered.length}/{conversations.length}
      </span>
    {/if}
  </div>

  {#if hasConversations}
    <label class="search">
      <span class="sr-only">{arabic ? 'البحث في المحادثات' : 'Search conversations'}</span>
      <input
        type="search"
        bind:value={query}
        autocomplete="off"
        placeholder={arabic ? 'ابحث في المحادثات' : 'Search conversations'}
        aria-label={arabic ? 'البحث في المحادثات' : 'Search conversations'}
        onkeydown={(event) => {
          if (event.key === 'Escape' && query) {
            event.preventDefault();
            query = '';
          }
        }}
      />
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
  {/if}

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
      <p>{arabic ? 'لا توجد محادثات بعد.' : 'No conversations yet.'}</p>
      <button class="ghost-button" onclick={oncreate}>{arabic ? 'ابدأ محادثة' : 'Start a conversation'}</button>
    </div>
  {:else if noMatches}
    <div class="empty">
      <p>{arabic ? 'لا توجد نتائج مطابقة.' : 'No matching conversations.'}</p>
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
          {#if menuOpenFor === chat.id}
            <div class="menu" role="menu" bind:this={menuElement}>
              <button role="menuitem" disabled>{arabic ? 'إعادة التسمية' : 'Rename'}</button>
              <button role="menuitem" disabled>{arabic ? 'حذف' : 'Delete'}</button>
              <p>
                {arabic ? 'تتوفّر إعادة التسمية والحذف بعد دعم الخادم.' : 'Rename and delete need backend support.'}
              </p>
            </div>
          {/if}
        </div>
      {/each}
    </nav>
  {/if}

  <p class="sidebar-foot">Voice Context Studio</p>
</div>
