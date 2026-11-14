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

  const searchLabel = $derived(arabic ? 'البحث في المحادثات' : 'Search conversations');
</script>

<div class="sidebar-inner chat-history" dir={arabic ? 'rtl' : 'ltr'}>
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
