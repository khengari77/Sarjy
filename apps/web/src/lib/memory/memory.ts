import type { components } from '../../../../../packages/contracts/api';
import { request } from '../api';

export type MemoryList = components['schemas']['MemoryList'];
export type MemoryListItem = components['schemas']['MemoryListItem'];
export type MemorySearchResult = components['schemas']['MemoryResult'];
export type ContextProposal = components['schemas']['ContextProposal'];
export type ContextConfirmation = components['schemas']['ContextConfirmation'];
export type ContextUndoResult = components['schemas']['ContextUndoResult'];

export interface MemorySearchHit extends MemorySearchResult {
  distance: number | null;
}

export interface MemoryLabels {
  stored: true;
  retrieved: boolean;
  included: boolean;
  excluded: boolean;
  summary: string;
}

export interface MemoryRow {
  item: MemoryListItem;
  labels: MemoryLabels;
}

const STATUS_LABELS: Record<string, string> = {
  active: 'Current version',
  superseded: 'Earlier version (corrected)',
  inactive: 'Inactive',
  excluded: 'Excluded',
};

export function shortId(value: string | null): string {
  if (!value) return '';
  return value.length > 12 ? `${value.slice(0, 8)}…` : value;
}

export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? 'Stored fact';
}

/**
 * The explorer must never conflate persistence, retrieval, actual context
 * inclusion and conversation exclusions. Every dimension is returned
 * separately; the summary only names the strongest true state.
 */
export function labelsFor(item: MemoryListItem, retrieved: boolean): MemoryLabels {
  const excluded = item.excluded_in_conversation === true;
  const included = !excluded && item.status === 'active' && item.included_in_conversation === true;
  let summary: string;
  if (item.status !== 'active') {
    summary = statusLabel(item.status);
  } else if (excluded) {
    summary = 'Excluded from this conversation';
  } else if (included) {
    summary = 'Included in the next compiled context';
  } else {
    summary = 'Stored only';
  }
  return { stored: true, retrieved, included, excluded, summary };
}

export function retrievedIds(results: readonly MemorySearchResult[]): Set<string> {
  return new Set(results.map((result) => result.id));
}

export function formatTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('en', {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function provenance(item: MemoryListItem): string {
  const parts = ['Saved from conversation ' + shortId(item.source_conversation_id)];
  if (item.source_turn_id) parts.push('source turn ' + shortId(item.source_turn_id));
  if (item.supersedes_id) parts.push('corrects ' + shortId(item.supersedes_id));
  return parts.join(' · ');
}

export function friendlyMemoryError(code: string): string {
  switch (code) {
    case 'EMBEDDINGS_NOT_CONFIGURED':
    case 'EMBEDDINGS_UNAVAILABLE':
      return 'Search is unavailable in this deployment. Your saved facts are still listed.';
    case 'REVISION_CONFLICT':
      return 'This conversation changed while you were deciding. Nothing was excluded; review the latest context and try again.';
    case 'OPERATION_ALREADY_RESTORED':
      return 'That exclusion was already undone.';
    case 'MEMORY_NOT_FOUND':
      return 'That saved fact no longer exists.';
    case 'CONTEXT_PROPOSALS_NOT_CONFIGURED':
      return 'Context changes are unavailable in this deployment. Nothing was changed.';
    case 'DAILY_BUDGET_EXHAUSTED':
      return 'The daily search allowance is exhausted. Nothing was changed.';
    default:
      return 'The memory service did not confirm the change. Nothing was changed.';
  }
}

export async function loadMemories(conversationId: string | null, offset = 0, limit = 50): Promise<MemoryList> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (conversationId) params.set('conversation_id', conversationId);
  return request<MemoryList>(`memories?${params.toString()}`);
}

export async function searchMemories(conversationId: string, query: string, limit = 10): Promise<MemorySearchHit[]> {
  return request<MemorySearchHit[]>('memories/search', {
    conversation_id: conversationId,
    query,
    limit,
  });
}

export async function previewExclusion(conversationId: string, memoryIds: readonly string[]): Promise<ContextProposal> {
  return request<ContextProposal>(`conversations/${conversationId}/context/exclusions`, {
    memory_ids: [...memoryIds],
  });
}

export async function confirmExclusion(
  conversationId: string,
  proposal: string,
  expectedRevision: number,
  idempotencyKey: string,
): Promise<ContextConfirmation> {
  return request<ContextConfirmation>(`conversations/${conversationId}/context/confirm`, {
    proposal,
    expected_revision: expectedRevision,
    idempotency_key: idempotencyKey,
  });
}

export async function undoExclusion(
  conversationId: string,
  operationId: string,
  expectedRevision: number,
  idempotencyKey: string,
): Promise<ContextUndoResult> {
  return request<ContextUndoResult>(`conversations/${conversationId}/context/undo`, {
    operation_id: operationId,
    expected_revision: expectedRevision,
    idempotency_key: idempotencyKey,
  });
}

export function newIdempotencyKey(kind: string): string {
  const suffix =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${kind}-${suffix}`.slice(0, 100);
}
