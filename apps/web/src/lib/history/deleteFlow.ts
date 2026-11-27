/** Index the next delete should focus: the deleted row's position, or the previous row. */
export function deleteFocusIndex(ids: string[], deletedId: string): number {
  return Math.max(0, ids.indexOf(deletedId));
}

/** Focus the row now at index, else the previous one, else New conversation. */
export function focusAfterDelete(root: ParentNode | null, index: number): void {
  if (!root) return;
  const items = root.querySelectorAll<HTMLButtonElement>('.chat-item');
  const target = items[index] ?? items[index - 1] ?? root.querySelector<HTMLButtonElement>('.new-chat');
  target?.focus();
}

/** Put focus on the first action (Cancel) of the confirm row, e.g. after a failed delete disabled the button. */
export function focusConfirmAction(menu: ParentNode | null): void {
  menu?.querySelector<HTMLButtonElement>('.menu-actions button')?.focus();
}
