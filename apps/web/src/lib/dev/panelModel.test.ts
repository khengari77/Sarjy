import { describe, expect, it } from 'vitest';
import { buildDiagnostics } from './panelModel';
import type { DevContextSnapshot, DevOperation } from './contextApi';

const context: DevContextSnapshot = { conversationId: 'c1', revision: 3, excludedIds: ['m2'] };

const operation: DevOperation = {
  id: 'op1',
  commandType: 'exclude',
  outcome: 'applied',
  revisionBefore: 2,
  revisionAfter: 3,
  createdAt: '2026-11-14T09:00:00Z',
  diff: { added: [], removed: ['m2'], kept: ['m1'], revisionBefore: 2, revisionAfter: 3 },
};

function section(model: ReturnType<typeof buildDiagnostics>, id: string) {
  const found = model.sections.find((entry) => entry.id === id);
  if (!found) throw new Error(`missing section ${id}`);
  return found;
}

describe('diagnostics view model', () => {
  it('renders context, operation history and the stored diff', () => {
    const model = buildDiagnostics({ context, operations: [operation], rendererTier: 'light' });
    expect(section(model, 'compiled').rows).toContainEqual({ label: 'Conversation revision', value: '3' });
    expect(section(model, 'compiled').rows).toContainEqual({ label: 'Excluded records', value: 'm2' });
    expect(section(model, 'diff').rows[0].value).toContain('removed: m2');
    expect(section(model, 'operations').rows[0].value).toContain('r2 → r3');
    expect(section(model, 'fallback').rows).toEqual([{ label: 'Renderer tier', value: 'light' }]);
    expect(model.operationCount).toBe(1);
  });

  it('labels API gaps as unavailable instead of inventing values', () => {
    const model = buildDiagnostics({ context, operations: [operation], rendererTier: 'enhanced' });
    for (const id of ['compiled', 'latency']) {
      const row = section(model, id).rows.find(
        (entry) => entry.label.includes('Unavailable') || entry.value.includes('Unavailable'),
      );
      expect(row, id).toBeTruthy();
    }
    expect(section(model, 'compiled').rows).toContainEqual({
      label: 'Retrieved vs included records',
      value: 'Unavailable — not exposed by the context API',
    });
    expect(section(model, 'compiled').rows).toContainEqual({
      label: 'Token counts',
      value: 'Unavailable — not exposed by the context API',
    });
    expect(section(model, 'latency').status).toBe('unavailable');
  });

  it('shows honest empty states when no operation exists', () => {
    const model = buildDiagnostics({
      context: { ...context, excludedIds: [] },
      operations: [],
      rendererTier: 'static',
    });
    expect(section(model, 'provenance')).toMatchObject({ status: 'empty', rows: [] });
    expect(section(model, 'diff').status).toBe('empty');
    expect(section(model, 'operations').status).toBe('empty');
    expect(section(model, 'compiled').rows).toContainEqual({ label: 'Excluded records', value: 'None' });
    expect(model.operationCount).toBe(0);
  });

  it('never emits provider, key or endpoint material', () => {
    const model = buildDiagnostics({ context, operations: [operation], rendererTier: 'light' });
    const serialized = JSON.stringify(model);
    for (const marker of [
      'sk-',
      'api_key',
      'apiKey',
      'secret',
      'bearer',
      'wss://',
      'http://',
      'openai',
      'elevenlabs',
    ]) {
      expect(serialized.toLowerCase()).not.toContain(marker.toLowerCase());
    }
  });
});
