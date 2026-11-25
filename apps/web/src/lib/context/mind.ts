import { shortId } from '../memory/memory';
import type { ContextBlock, ContextBudget, ContextExcluded, ContextIncluded, ContextKind } from './context';

export type ChipState = 'included' | 'hidden' | 'forgotten' | 'over_budget' | 'not_included';

export interface ContextChip {
  id: string;
  state: ChipState;
  label: string;
  reason: string;
  text: string;
  accessible: string;
}

const CHIP_LABELS: Record<ChipState, string> = {
  included: 'Included',
  hidden: 'Hidden by you',
  forgotten: 'Forgotten in this chat',
  over_budget: 'Over budget',
  not_included: 'Not included',
};

const REASONS: Record<string, string> = {
  budget: 'Over the character budget',
  budget_or_tool_policy: 'Over the budget or blocked by tool policy',
  conversation_exclusion: 'Forgotten in this chat',
  hidden: 'Hidden by you',
  deleted: 'Deleted',
  lineage: 'A related memory changed',
  older_than_history_limit: 'Older than the history window',
  source_of_blocked_memory: 'Source of a hidden memory',
  derived_from_blocked_record: 'Derived from a hidden record',
};

export function humanReason(reason: string): string {
  return REASONS[reason] ?? reason.replaceAll('_', ' ');
}

function chipState(block: ContextBlock): ChipState {
  if (block.state === 'included') return 'included';
  if (block.state === 'excluded') return 'forgotten';
  if (block.state === 'blocked') return 'hidden';
  if (block.state === 'not_sent') return 'over_budget';
  return 'not_included';
}

function excerpt(value: string, limit = 64): string {
  const clean = value.replace(/\s+/g, ' ').trim();
  return clean.length > limit ? `${clean.slice(0, limit - 1)}…` : clean;
}

export function chipFor(block: ContextBlock): ContextChip {
  const state = chipState(block);
  const label = CHIP_LABELS[state];
  const reason = block.reason ? humanReason(block.reason) : '';
  const text = excerpt(block.text);
  const suffix = reason && reason !== label ? `. ${reason}` : '';
  return { id: block.id, state, label, reason, text, accessible: `${text} — ${label}${suffix}` };
}

/** Memory blocks only: turn and tool rows belong to the transcript receipt. */
export function memoryChips(blocks: readonly ContextBlock[]): ContextChip[] {
  return blocks.filter((block) => block.kind === 'memory').map(chipFor);
}

export interface MeterValues {
  used: number;
  limit: number;
  now: number;
  min: number;
  max: number;
  percent: number;
  over: boolean;
}

export function meterValues(budget: ContextBudget): MeterValues {
  const used = Math.max(0, Math.round(budget.used_chars));
  const limit = Math.max(0, Math.round(budget.limit_chars));
  const now = Math.min(used, limit);
  return {
    used,
    limit,
    now,
    min: 0,
    max: Math.max(limit, 1),
    percent: limit > 0 ? (now / limit) * 100 : 0,
    over: used > limit,
  };
}

export function contextKindLabel(kind: ContextKind): string {
  if (kind === 'memory') return 'Memory';
  if (kind === 'tool') return 'Tool result';
  return 'History';
}

/** Diff ids resolve to the label the same receipt already shows, else a short id. */
export function diffLabel(
  id: string,
  included: readonly ContextIncluded[],
  excluded: readonly ContextExcluded[],
): string {
  const found = included.find((item) => item.id === id);
  if (found) return `${contextKindLabel(found.kind)}: ${excerpt(found.text, 48)}`;
  const denied = excluded.find((item) => item.id === id);
  if (denied) return `Excluded: ${humanReason(denied.reason)}`;
  return shortId(id);
}
