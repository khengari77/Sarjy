import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createChangeToastTiming } from './changeToastTiming';

function harness() {
  let changeId: string | null = null;
  let undoing = false;
  let error = '';
  const dismiss = vi.fn(() => {
    changeId = null;
  });
  const clearStatus = vi.fn();
  const timing = createChangeToastTiming({
    isUndoing: () => undoing,
    hasError: () => error !== '',
    dismiss,
    clearStatus,
  });
  return {
    timing,
    dismiss,
    clearStatus,
    setChange(id: string | null) {
      changeId = id;
      timing.syncChange(id);
    },
    getChange: () => changeId,
    startUndo() {
      undoing = true;
      timing.syncChange(changeId);
    },
    failUndo(message: string) {
      undoing = false;
      error = message;
      timing.syncChange(changeId);
    },
  };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe('change toast auto-dismiss', () => {
  it('keeps the toast and its undo failure visible at 8s and for 8s after the failure', () => {
    const app = harness();
    app.setChange('change-1');
    app.startUndo();

    vi.advanceTimersByTime(7900);
    app.failUndo("Couldn't undo that change. Nothing was changed.");

    vi.advanceTimersByTime(100);
    expect(app.dismiss).not.toHaveBeenCalled();
    expect(app.getChange()).toBe('change-1');

    vi.advanceTimersByTime(7900);
    expect(app.dismiss).not.toHaveBeenCalled();
    expect(app.getChange()).toBe('change-1');
  });

  it('does not dismiss while an undo is still in flight', () => {
    const app = harness();
    app.setChange('change-1');
    app.startUndo();

    vi.advanceTimersByTime(10_000);

    expect(app.dismiss).not.toHaveBeenCalled();
  });

  it('dismisses a change with no undo attempt after 8 seconds', () => {
    const app = harness();
    app.setChange('change-1');

    vi.advanceTimersByTime(7999);
    expect(app.dismiss).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    expect(app.dismiss).toHaveBeenCalledTimes(1);
  });
});

describe('Undone status overlay', () => {
  it('clears Undone after about 3 seconds', () => {
    const app = harness();
    app.timing.syncStatus('Undone.');

    vi.advanceTimersByTime(2999);
    expect(app.clearStatus).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    expect(app.clearStatus).toHaveBeenCalledTimes(1);
  });

  it('clears Undone on the same timer when the toast was dismissed during the undo', () => {
    const app = harness();
    app.setChange('change-1');
    app.startUndo();
    app.timing.syncChange(null);
    app.timing.syncStatus('Undone.');

    vi.advanceTimersByTime(3000);

    expect(app.clearStatus).toHaveBeenCalledTimes(1);
  });

  it('cancels the status timer when a new change arrives', () => {
    const app = harness();
    app.timing.syncStatus('Undone.');
    app.setChange('change-2');

    vi.advanceTimersByTime(3000);

    expect(app.clearStatus).not.toHaveBeenCalled();
  });

  it('cancels the status timer on destroy', () => {
    const app = harness();
    app.timing.syncStatus('Undone.');
    app.timing.destroy();

    vi.advanceTimersByTime(3000);

    expect(app.clearStatus).not.toHaveBeenCalled();
  });
});
