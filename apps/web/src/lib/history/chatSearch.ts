import type { Conversation } from '../api';

/**
 * Client-side history search. Arabic titles are matched with diacritics
 * removed and common letter variants folded, so "احمد" finds "أحمد".
 */
export function normalizeChatQuery(value: string): string {
  return value
    .normalize('NFKD')
    .replace(/[\u064b-\u065f\u0670\u06d6-\u06ed]/g, '')
    .replace(/[\u0640\u200c-\u200f]/g, '')
    .replace(/[أإآٱ]/g, 'ا')
    .replace(/ى/g, 'ي')
    .replace(/ة/g, 'ه')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase();
}

export function filterConversations(conversations: Conversation[], query: string): Conversation[] {
  const needle = normalizeChatQuery(query);
  if (!needle) return conversations;
  return conversations.filter((chat) => normalizeChatQuery(chat.title).includes(needle));
}
