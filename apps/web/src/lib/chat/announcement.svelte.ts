/**
 * Debounced live-region announcement for the voice status line.
 *
 * Each distinct label is announced once, after a short delay, so fast status
 * churn (thinking → speaking) does not spam the screen reader.
 */
export function createAnnouncement(readLabel: () => string, delay = 300) {
  let message = $state('');
  let last = '';

  $effect(() => {
    const label = readLabel();
    if (label === last) return;
    const timer = setTimeout(() => {
      last = label;
      message = label;
    }, delay);
    return () => clearTimeout(timer);
  });

  return {
    get message() {
      return message;
    },
  };
}
