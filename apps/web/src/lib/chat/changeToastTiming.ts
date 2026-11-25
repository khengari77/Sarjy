/** Timer rules for the change toast, kept out of the component so they are testable. */

export const CHANGE_TOAST_MS = 8000;
export const CHANGE_STATUS_MS = 3000;

export interface ChangeToastTimingHandlers {
  isUndoing: () => boolean;
  hasError: () => boolean;
  dismiss: () => void;
  clearStatus: () => void;
}

export function createChangeToastTiming(handlers: ChangeToastTimingHandlers) {
  let dismissTimer: ReturnType<typeof setTimeout> | null = null;
  let statusTimer: ReturnType<typeof setTimeout> | null = null;
  let currentId: string | null = null;

  function clearDismiss() {
    if (dismissTimer !== null) {
      clearTimeout(dismissTimer);
      dismissTimer = null;
    }
  }

  function clearStatus() {
    if (statusTimer !== null) {
      clearTimeout(statusTimer);
      statusTimer = null;
    }
  }

  function syncChange(id: string | null) {
    const isNew = id !== currentId;
    currentId = id;
    clearDismiss();
    if (id === null) return;
    // A new change replaces the old toast, including any "Undone." still showing.
    if (isNew) clearStatus();
    dismissTimer = setTimeout(() => {
      dismissTimer = null;
      // Never erase an in-flight undo or a just-displayed failure.
      if (handlers.isUndoing() || handlers.hasError()) return;
      handlers.dismiss();
    }, CHANGE_TOAST_MS);
  }

  function syncStatus(status: string) {
    clearStatus();
    if (!status) return;
    statusTimer = setTimeout(() => {
      statusTimer = null;
      handlers.clearStatus();
    }, CHANGE_STATUS_MS);
  }

  function destroy() {
    clearDismiss();
    clearStatus();
  }

  return { syncChange, syncStatus, destroy };
}
