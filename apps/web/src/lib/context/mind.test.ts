import { render } from 'svelte/server';
import { describe, expect, it } from 'vitest';
import MindChips from './MindChips.svelte';
import { RETRACTION_CHIP_LABEL, RETRACTION_CHIP_TOOLTIP } from './copy';
import { chipFor, humanReason, memoryChips, type ChipState } from './mind';
import type { ContextBlock } from './context';

function block(overrides: Partial<ContextBlock> = {}): ContextBlock {
  return {
    id: 'memory-1',
    kind: 'memory',
    role: 'memory',
    text: 'Trip to Tripoli',
    truncated: false,
    estimated_tokens: 4,
    state: 'included',
    reason: null,
    ...overrides,
  };
}

interface Case {
  kind: string;
  state: string;
  reason: string | null;
  chipState: ChipState;
  label: string;
  reasonText: string;
}

// Every (kind, state, reason) apps/api/app/services/context_blocks.py can emit,
// plus unknown values a future server may add.
const CASES: Case[] = [
  { kind: 'memory', state: 'included', reason: null, chipState: 'included', label: 'Included', reasonText: '' },
  {
    kind: 'memory',
    state: 'not_sent',
    reason: 'budget',
    chipState: 'over_budget',
    label: 'Over budget',
    reasonText: 'Over the character budget',
  },
  {
    kind: 'memory',
    state: 'blocked',
    reason: 'hidden',
    chipState: 'hidden',
    label: 'Hidden by you',
    reasonText: 'Hidden by you',
  },
  {
    kind: 'memory',
    state: 'blocked',
    reason: 'deleted',
    chipState: 'deleted',
    label: 'Deleted',
    reasonText: 'Deleted',
  },
  {
    kind: 'memory',
    state: 'blocked',
    reason: 'lineage',
    chipState: 'replaced',
    label: 'Replaced by a newer version',
    reasonText: 'Replaced by a newer version',
  },
  {
    kind: 'memory',
    state: 'excluded',
    reason: 'conversation_exclusion',
    chipState: 'forgotten',
    label: 'Forgotten in this chat',
    reasonText: 'Forgotten in this chat',
  },
  { kind: 'turn', state: 'included', reason: null, chipState: 'included', label: 'Included', reasonText: '' },
  {
    kind: 'turn',
    state: 'not_sent',
    reason: 'budget',
    chipState: 'over_budget',
    label: 'Over budget',
    reasonText: 'Over the character budget',
  },
  {
    kind: 'turn',
    state: 'outside_window',
    reason: 'older_than_history_limit',
    chipState: 'not_included',
    label: 'Not included',
    reasonText: 'Older than the history window',
  },
  {
    kind: 'turn',
    state: 'blocked',
    reason: 'source_of_blocked_memory',
    chipState: 'not_included',
    label: 'Not included',
    reasonText: 'Source of a hidden memory',
  },
  {
    kind: 'turn',
    state: 'blocked',
    reason: 'derived_from_blocked_record',
    chipState: 'not_included',
    label: 'Not included',
    reasonText: 'Derived from a hidden record',
  },
  { kind: 'tool_result', state: 'included', reason: null, chipState: 'included', label: 'Included', reasonText: '' },
  {
    kind: 'tool_result',
    state: 'not_sent',
    reason: 'budget_or_tool_policy',
    chipState: 'over_budget',
    label: 'Over budget',
    reasonText: 'Over the budget or blocked by tool policy',
  },
  {
    kind: 'tool_result',
    state: 'blocked',
    reason: 'derived_from_blocked_record',
    chipState: 'not_included',
    label: 'Not included',
    reasonText: 'Derived from a hidden record',
  },
  {
    kind: 'memory',
    state: 'future_state',
    reason: 'future_reason',
    chipState: 'not_included',
    label: 'Not included',
    reasonText: 'future reason',
  },
  {
    kind: 'turn',
    state: 'blocked',
    reason: 'future_reason',
    chipState: 'not_included',
    label: 'Not included',
    reasonText: 'future reason',
  },
];

describe('context chip mapping', () => {
  it.each(CASES)('$kind / $state / $reason maps to $label', ({ kind, state, reason, chipState, label, reasonText }) => {
    const chip = chipFor(block({ kind, state, reason }));
    expect(chip.state).toBe(chipState);
    expect(chip.label).toBe(label);
    expect(chip.reason).toBe(reasonText);
  });

  it('never throws for an unknown reason and keeps its text', () => {
    expect(humanReason('a_new_reason')).toBe('a new reason');
    const chip = chipFor(block({ state: 'blocked', reason: 'a_new_reason' }));
    expect(chip.label).toBe('Not included');
    expect(chip.reason).toBe('a new reason');
    expect(chip.accessible).toContain('a new reason');
  });
});

describe('retraction chips', () => {
  const secret = 'The user moved to 12 Secret Street';

  it('labels a retraction without exposing its block text', () => {
    const [chip] = memoryChips([
      block({ id: 'retraction:memory-1', kind: 'retraction', state: 'included', text: secret }),
    ]);
    expect(chip.retraction).toBe(true);
    expect(chip.label).toBe(RETRACTION_CHIP_LABEL);
    expect(chip.tooltip).toBe(RETRACTION_CHIP_TOOLTIP);
    expect(chip.text).toBe('');
    expect(JSON.stringify(chip)).not.toContain(secret);
  });

  it('renders the retraction chip in the DOM without its text or aria output', () => {
    const chips = memoryChips([
      block({ id: 'retraction:memory-1', kind: 'retraction', state: 'included', text: secret }),
    ]);
    const { body } = render(MindChips, { props: { chips } });

    expect(body).toContain(RETRACTION_CHIP_LABEL);
    expect(body).toContain(RETRACTION_CHIP_TOOLTIP);
    expect(body).toContain('data-retraction="true"');
    expect(body).not.toContain(secret);
    expect(body).not.toContain('Secret Street');
  });
});
