import { ORB_PALETTES, type ThemeName } from '../palette';

/**
 * Palette access for the renderer. The theme itself comes from the shell's
 * resolved presentation (`OrbRenderState.theme`); the renderer never reads
 * `data-theme` or `prefers-color-scheme`, so it cannot disagree with the shell.
 */
export function paletteFor(theme: ThemeName): readonly string[] {
  return ORB_PALETTES[theme];
}
