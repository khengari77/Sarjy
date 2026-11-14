/**
 * Pure view model for the read-only diagnostics panel.
 *
 * Every value shown comes from a context/operations response or from the
 * renderer tier computed in the browser. Anything the API does not return is
 * labelled unavailable; no latency, token count or retrieved record is guessed.
 */

import type { RendererTier } from '../orb/rendererTier';
import type { DevContextSnapshot, DevOperation } from './contextApi';

export type SectionStatus = 'ready' | 'empty' | 'unavailable';

export interface DiagnosticRow {
  label: string;
  value: string;
}

export interface DiagnosticSection {
  id: 'compiled' | 'provenance' | 'diff' | 'operations' | 'fallback' | 'latency';
  title: string;
  status: SectionStatus;
  rows: DiagnosticRow[];
  note: string;
}

export interface DiagnosticsModel {
  context: DevContextSnapshot;
  sections: DiagnosticSection[];
  operationCount: number;
}

const UNAVAILABLE = 'Unavailable — not exposed by the context API';

function revisionLabel(value: number | null): string {
  return value === null ? 'unknown' : `r${value}`;
}

function idList(ids: string[]): string {
  return ids.length ? ids.join(', ') : 'None';
}

function operationRows(operations: DevOperation[]): DiagnosticRow[] {
  return operations.map((operation) => ({
    label: `${operation.commandType} · ${operation.createdAt}`,
    value: `${operation.outcome} · ${revisionLabel(operation.revisionBefore)} → ${revisionLabel(operation.revisionAfter)}`,
  }));
}

function diffRows(operations: DevOperation[]): DiagnosticRow[] {
  const rows: DiagnosticRow[] = [];
  for (const operation of operations) {
    const diff = operation.diff;
    if (!diff) continue;
    rows.push({
      label: `Revision diff · ${revisionLabel(diff.revisionBefore)} → ${revisionLabel(diff.revisionAfter)}`,
      value: `added: ${idList(diff.added)} | removed: ${idList(diff.removed)} | kept: ${idList(diff.kept)}`,
    });
  }
  return rows;
}

export function buildDiagnostics(input: {
  context: DevContextSnapshot;
  operations: DevOperation[];
  rendererTier: RendererTier;
}): DiagnosticsModel {
  const { context, operations, rendererTier } = input;
  const diffs = diffRows(operations);
  return {
    context,
    operationCount: operations.length,
    sections: [
      {
        id: 'compiled',
        title: 'Compiled context',
        status: 'ready',
        rows: [
          { label: 'Conversation revision', value: String(context.revision) },
          { label: 'Excluded records', value: context.excludedIds.length ? idList(context.excludedIds) : 'None' },
          { label: 'Retrieved vs included records', value: UNAVAILABLE },
          { label: 'Token counts', value: UNAVAILABLE },
        ],
        note: 'Revision and exclusions come from the context endpoint. The API does not return compiled record text, so retrieved/included groups are reported as unavailable instead of guessed.',
      },
      {
        id: 'provenance',
        title: 'Provenance',
        status: diffs.length ? 'ready' : 'empty',
        rows: diffs.map((row) => ({ label: row.label, value: row.value })),
        note: diffs.length
          ? 'Record IDs and counts from stored context-operation diffs.'
          : 'No context-operation diff has been recorded for this conversation.',
      },
      {
        id: 'diff',
        title: 'Before / after diff',
        status: diffs.length ? 'ready' : 'empty',
        rows: diffRows(operations),
        note: diffs.length
          ? 'Added, removed and kept record IDs as stored by the last context operation.'
          : 'Nothing to diff yet: no stored context operation for this conversation.',
      },
      {
        id: 'operations',
        title: 'Revision & operation history',
        status: operations.length ? 'ready' : 'empty',
        rows: operationRows(operations),
        note: operations.length
          ? 'Read-only history from the context operations endpoint.'
          : 'No context operations recorded for this conversation.',
      },
      {
        id: 'fallback',
        title: 'Renderer & fallback',
        status: 'ready',
        rows: [{ label: 'Renderer tier', value: rendererTier }],
        note: 'Computed in this browser from reduced-motion and WebGL availability. Fallback only moves downward: enhanced → light → static.',
      },
      {
        id: 'latency',
        title: 'Latency',
        status: 'unavailable',
        rows: [{ label: 'Context round trip', value: UNAVAILABLE }],
        note: 'These endpoints do not return a latency breakdown, so no timing is displayed.',
      },
    ],
  };
}
