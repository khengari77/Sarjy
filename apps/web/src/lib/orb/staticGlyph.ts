/**
 * Glyph for the static (reduced-motion / failed) renderer. Kept out of the
 * component so VoiceOrb stays within the file-length budget.
 */
export function staticGlyph(phase: string): string {
  switch (phase) {
    case 'connecting':
      return '◌';
    case 'listening':
      return '◉';
    case 'thinking':
      return '✦';
    case 'speaking':
      return '◍';
    case 'disconnected':
      return '⊘';
    case 'error':
      return '!';
    default:
      return '●';
  }
}
