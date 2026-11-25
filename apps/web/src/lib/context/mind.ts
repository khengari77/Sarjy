import { shortId } from '../memory/memory';
import type { ContextBlock, ContextBudget, ContextExcluded, ContextIncluded, ContextKind } from './context';
import { RETRACTION_CHIP_LABEL, RETRACTION_CHIP_TOOLTIP } from './copy';

export type ChipState = 'included' | 'hidden' | 'forgotten' | 'deleted' | 'replaced' | 'over_budget' | 'not_included';

export interface ContextChip {
  id: string;
  state: ChipState;
  label: string;
  reason: string;
  text: string;
  accessible: string;
  /** Short hover text; the accessible label is the fallback. */
  tooltip?: string;
  /** Server-side retraction reminder, rendered without its block text. */
  retraction: boolean;
}

const CHIP_LABELS: Record<ChipState, string> = {
  included: 'Included',
  hidden: 'Hidden by you',
  forgotten: 'Forgotten in this chat',
  deleted: 'Deleted',
  replaced: 'Replaced by a newer version',
  over_budget: 'Over budget',
  not_included: 'Not included',
};

const REASONS: Record<string, string> = {
  budget: 'Over the character budget',
  budget_or_tool_policy: 'Over the budget or blocked by tool policy',
  conversation_exclusion: 'Forgotten in this chat',
  hidden: 'Hidden by you',
  deleted: 'Deleted',
  lineage: 'Replaced by a newer version',
  older_than_history_limit: 'Older than the history window',
  source_of_blocked_memory: 'Source of a hidden memory',
  derived_from_blocked_record: 'Derived from a hidden record',
};

export function humanReason(reason: string): string {
  return REASONS[reason] ?? reason.replaceAll('_', ' ');
}

/** A blocked row means different things by reason; only "hidden" is a user hide. */
function chipState(block: ContextBlock): ChipState {
  if (block.state === 'included') return 'included';
  if (block.state === 'not_sent') {
    return block.reason === 'budget' || block.reason === 'budget_or_tool_policy' ? 'over_budget' : 'not_included';
  }
  if (block.state === 'excluded' || block.reason === 'conversation_exclusion') return 'forgotten';
  if (block.state === 'blocked') {
    if (block.reason === 'hidden') return 'hidden';
    if (block.reason === 'deleted') return 'deleted';
    if (block.reason === 'lineage') return 'replaced';
    return 'not_included';
  }
  return 'not_included';
}

function excerpt(value: string, limit = 64): string {
  const clean = value.replace(/\s+/g, ' ').trim();
  return clean.length > limit ? `${clean.slice(0, limit - 1)}…` : clean;
}

function retractionChip(block: ContextBlock): ContextChip {
  return {
    id: block.id,
    state: block.state === 'included' ? 'included' : 'not_included',
    label: RETRACTION_CHIP_LABEL,
    reason: '',
    text: '',
    accessible: `${RETRACTION_CHIP_LABEL}. ${RETRACTION_CHIP_TOOLTIP}`,
    tooltip: RETRACTION_CHIP_TOOLTIP,
    retraction: true,
  };
}

export function chipFor(block: ContextBlock): ContextChip {
  if (block.kind === 'retraction') return retractionChip(block);
  const state = chipState(block);
  const label = CHIP_LABELS[state];
  const reason = block.reason ? humanReason(block.reason) : '';
  const text = excerpt(block.text);
  const suffix = reason && reason !== label ? `. ${reason}` : '';
  return {
    id: block.id,
    state,
    label,
    reason,
    text,
    accessible: `${text} — ${label}${suffix}`,
    retraction: false,
  };
}

/** Memory and retraction rows: turns and tool results belong to the transcript receipt. */
export function memoryChips(blocks: readonly ContextBlock[]): ContextChip[] {
  return blocks.filter((block) => block.kind === 'memory' || block.kind === 'retraction').map(chipFor);
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
