<script lang="ts">
import { tick } from 'svelte';
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

<div class="sidebar-inner" dir={arabic ? 'rtl' : 'ltr'}>
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
        placeholder={arabic ? 'ابحث في المحادثات' : 'Search conversations'}
        aria-label={arabic ? 'البحث في المحادثات' : 'Search conversations'}
      />
      {#if query}
        <button type="button" class="clear" onclick={() => (query = '')} aria-label={arabic ? 'مسح البحث' : 'Clear search'}>
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
      {#each filtered as chat}
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
                {arabic
                  ? 'تتوفّر إعادة التسمية والحذف بعد دعم الخادم.'
                  : 'Rename and delete need backend support.'}
              </p>
            </div>
          {/if}
        </div>
      {/each}
    </nav>
  {/if}

  <p class="sidebar-foot">Voice Context Studio</p>
</div>

<style>
  .new-chat {
    font: inherit;
    min-height: var(--control-height);
    padding: 10px 16px;
    border-radius: var(--radius-control);
    border: 1px solid color-mix(in srgb, var(--orb-idle) 45%, var(--border-subtle));
    background: color-mix(in srgb, var(--orb-idle) 18%, transparent);
    color: var(--text-primary);
    font-weight: 600;
    cursor: pointer;
    transition:
      background var(--transition-quick),
      opacity var(--transition-quick);
  }

  .new-chat:hover:enabled {
    background: color-mix(in srgb, var(--orb-idle) 28%, transparent);
  }

  .new-chat:disabled {
    opacity: 0.55;
    cursor: not-allowed;
  }

  .chat-list {
    display: flex;
    flex-direction: column;
    gap: 2px;
    margin: 0;
    padding: 0;
    list-style: none;
  }

  .chat-item {
    display: block;
    width: 100%;
    min-width: 0;
    padding: 9px 10px;
    border: 1px solid transparent;
    border-radius: 8px;
    background: transparent;
    color: var(--text-muted);
    font: inherit;
    font-size: 14px;
    text-align: start;
    cursor: pointer;
    transition:
      background var(--transition-quick),
      color var(--transition-quick),
      border-color var(--transition-quick);
  }

  .chat-item .title {
    display: block;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .chat-item:hover:enabled {
    background: var(--surface-hover);
    color: var(--text-primary);
  }

  .chat-item.chosen,
  .chat-item[aria-current='page'] {
    background: color-mix(in srgb, var(--orb-listening) 16%, transparent);
    border-color: color-mix(in srgb, var(--orb-listening) 38%, var(--border-subtle));
    box-shadow: inset 3px 0 0 var(--orb-listening);
    color: var(--text-primary);
    font-weight: 600;
  }

  .chat-item:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }

  .history-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px;
  }

  .count {
    color: var(--text-muted);
    font-size: 11px;
  }

  .search {
    position: relative;
    display: block;
  }

  .search input {
    width: 100%;
    padding: 8px 30px 8px 10px;
    border-radius: 8px;
    border: 1px solid var(--border-subtle);
    background: var(--surface-sunken, transparent);
    color: var(--text-primary);
    font: inherit;
    font-size: 13px;
  }

  .clear {
    position: absolute;
    inset-inline-end: 4px;
    top: 50%;
    transform: translateY(-50%);
    border: 0;
    background: transparent;
    color: var(--text-muted);
    cursor: pointer;
    padding: 4px 6px;
  }

  .chat-row {
    position: relative;
    display: flex;
    align-items: center;
    gap: 2px;
  }

  .chat-row .chat-item {
    flex: 1;
    min-width: 0;
  }

  .menu-toggle {
    border: 0;
    background: transparent;
    color: var(--text-muted);
    cursor: pointer;
    padding: 6px 8px;
    border-radius: 6px;
  }

  .menu-toggle:hover,
  .menu-toggle:focus-visible {
    background: var(--surface-elevated);
    color: var(--text-primary);
  }

  .menu {
    position: absolute;
    inset-inline-end: 0;
    top: calc(100% + 4px);
    z-index: 20;
    min-width: 200px;
    padding: 6px;
    border-radius: 10px;
    border: 1px solid var(--border-subtle);
    background: var(--surface-elevated);
    box-shadow: 0 10px 30px rgb(0 0 0 / 0.35);
  }

  .menu button {
    display: block;
    width: 100%;
    text-align: start;
    padding: 6px 8px;
    border: 0;
    border-radius: 6px;
    background: transparent;
    color: var(--text-muted);
    font: inherit;
    font-size: 13px;
  }

  .menu p {
    margin: 6px 8px 2px;
    color: var(--text-muted);
    font-size: 11px;
    line-height: 1.4;
  }

  .empty {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 12px 4px;
    color: var(--text-muted);
    font-size: 13px;
  }

  .empty p {
    margin: 0;
  }

  .skeleton {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 8px 0;
  }

  .skeleton span {
    height: 32px;
    border-radius: 8px;
    background: linear-gradient(90deg, var(--surface-elevated), transparent);
    animation: pulse 1.2s ease-in-out infinite;
  }

  @keyframes pulse {
    50% {
      opacity: 0.45;
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .skeleton span {
      animation: none;
    }
  }
</style>
