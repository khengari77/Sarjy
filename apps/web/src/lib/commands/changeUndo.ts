import { undoChange as undoChangeRequest } from '../chat/session';
import type { CommandState, CommandStore } from './commandStore';

export interface UndoContext {
  state: CommandState;
  store: CommandStore;
  undoing: string[];
  setError: (value: string) => void;
  setStatus: (value: string) => void;
  onUndone: () => void;
}

function changeFailureText(detail: string): string {
  if (detail === 'CHANGE_SUPERSEDED' || detail === 'HTTP_409') {
    return 'That memory changed since, so I left it alone.';
  }
  if (detail === 'CHANGE_NOT_FOUND' || detail === 'HTTP_404') return 'That change is no longer available.';
  return "Couldn't undo that change. Nothing was changed.";
}

/**
 * Undo one auto-saved memory change. The in-flight guard is per id so a newer
 * change stays undoable while an older request is still pending, and late
 * results only report on the change they were started for.
 */
export async function performUndo(id: string, context: UndoContext): Promise<boolean> {
  const { state, store, undoing } = context;
  if (undoing.includes(id)) return false;
  undoing.push(id);
  const isCurrentChange = () => state.change?.id === id;
  if (isCurrentChange()) {
    context.setError('');
    context.setStatus('');
  }
  try {
    await undoChangeRequest(id);
    const wasCurrent = isCurrentChange();
    store.dismissChange(id);
    context.onUndone();
    // A dismissed toast still gets its confirmation; a newer change does not.
    if (wasCurrent || state.change === null) context.setStatus('Undone.');
    return true;
  } catch (error) {
    if (isCurrentChange()) {
      context.setError(changeFailureText(error instanceof Error ? error.message : 'CHANGE_UNDO_FAILED'));
    }
    return false;
  } finally {
    undoing.splice(undoing.indexOf(id), 1);
  }
}
