/** One close implementation, owned by the shell and used by both drawer UIs. */
export const DRAWER_CLOSE_CONTEXT = 'shell-close-panel';

export type ClosePanel = (panel: 'left' | 'right') => Promise<void>;
