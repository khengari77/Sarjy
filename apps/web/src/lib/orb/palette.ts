/**
 * Theme-aware Living Cloud palette contract.
 *
 * Values trace to the v2.1 design kit (`design/tokens/tokens.json`,
 * `themes.*.orbPalette`) and are mirrored in `ui/tokens.css` as
 * `--orb-palette-1..6` / `--orb-amber`. `palette.test.ts` parses that file and
 * fails if the two drift, so the renderer and the shell cannot disagree about
 * a theme's colours.
 *
 * The renderer requires literal 6-digit hex. Never pass CSS `color-mix()` or
 * named colours across this boundary (regression B01). For the same reason the
 * semantic colours below reuse the shell token *values* rather than resolving
 * `var(--status-error)` at runtime.
 */

export type ThemeName = 'light' | 'dark';

/** 4-6 blended colours per theme: 2 dominant + 1-3 accents. */
export const ORB_PALETTES: Record<ThemeName, readonly string[]> = {
  light: ['#9cdbff', '#7cdcc7', '#ffd37e', '#ff9ab0', '#b5a0ff', '#d6f6ff'],
  dark: ['#548fff', '#3b6fff', '#9a74f7', '#baa7ff', '#57d7d2', '#f67c9f'],
};

export function orbPalette(theme: ThemeName): readonly string[] {
  return ORB_PALETTES[theme];
}

/** Dominant pair used for the cloud body; the remainder are accents. */
export function orbDominant(theme: ThemeName): readonly [string, string] {
  const palette = ORB_PALETTES[theme];
  return [palette[0], palette[1]];
}

/** Accent colours, in stable order, for wisps and highlights. */
export function orbAccents(theme: ThemeName): readonly string[] {
  return ORB_PALETTES[theme].slice(2);
}

/** Voice phases that carry a Living Cloud palette weighting. */
export type LivingPhase = 'idle' | 'connecting' | 'listening' | 'thinking' | 'speaking' | 'disconnected';

/**
 * One palette slot and how strongly it contributes to the phase.
 * Weights for a phase sum to 1; indexes refer to `ORB_PALETTES[theme]`.
 */
export interface PaletteWeight {
  readonly index: number;
  readonly weight: number;
}

type PhaseWeights = Record<LivingPhase, readonly PaletteWeight[]>;

/**
 * Phase -> palette weighting table shared by the state derivation (#38) and
 * the renderer (#40). Derived from docs 01 ("Palette weighting") and 02:
 *
 * - idle       balanced theme palette
 * - connecting more sky blue / periwinkle
 * - listening  cyan / electric blue energised
 * - thinking   violet indigo energised
 * - speaking   mint/green highlights over a blue/violet core
 * - disconnected pale slot, later faded toward the theme slate
 *
 * Both themes keep the same semantic intent but use their own six colours, so
 * a phase change never borrows the other theme's hue.
 */
export const PHASE_PALETTE_WEIGHTS: Record<ThemeName, PhaseWeights> = {
  light: {
    idle: [
      { index: 0, weight: 0.5 },
      { index: 1, weight: 0.3 },
      { index: 5, weight: 0.2 },
    ],
    connecting: [
      { index: 0, weight: 0.5 },
      { index: 5, weight: 0.3 },
      { index: 4, weight: 0.2 },
    ],
    listening: [
      { index: 0, weight: 0.6 },
      { index: 5, weight: 0.4 },
    ],
    thinking: [
      { index: 4, weight: 0.7 },
      { index: 0, weight: 0.3 },
    ],
    speaking: [
      { index: 1, weight: 0.6 },
      { index: 0, weight: 0.4 },
    ],
    disconnected: [{ index: 5, weight: 1 }],
  },
  dark: {
    idle: [
      { index: 0, weight: 0.45 },
      { index: 1, weight: 0.35 },
      { index: 3, weight: 0.2 },
    ],
    connecting: [
      { index: 0, weight: 0.5 },
      { index: 2, weight: 0.3 },
      { index: 3, weight: 0.2 },
    ],
    listening: [
      { index: 0, weight: 0.55 },
      { index: 4, weight: 0.45 },
    ],
    thinking: [
      { index: 2, weight: 0.65 },
      { index: 3, weight: 0.35 },
    ],
    speaking: [
      { index: 4, weight: 0.55 },
      { index: 1, weight: 0.45 },
    ],
    disconnected: [{ index: 3, weight: 1 }],
  },
};

/** The weighting table for one theme/phase pair. */
export function orbPhaseWeights(theme: ThemeName, phase: LivingPhase): readonly PaletteWeight[] {
  return PHASE_PALETTE_WEIGHTS[theme][phase];
}

/**
 * Non-phase colours, tied to the shell's semantic tokens. The orb palette has
 * no error/amber/slate slot, so these reuse the theme tokens instead of
 * inventing a second palette:
 *
 * - memory/confirmation -> `--orb-amber`
 * - error               -> `--status-error`
 * - muted/disconnected  -> `--text-muted`
 */
export interface OrbStateColors {
  readonly memory: string;
  readonly confirmation: string;
  readonly error: string;
  readonly muted: string;
  readonly disconnected: string;
}

export const ORB_STATE_COLORS: Record<ThemeName, OrbStateColors> = {
  light: {
    memory: '#ffd37e',
    confirmation: '#ffd37e',
    error: '#c94767',
    muted: '#516b8f',
    disconnected: '#516b8f',
  },
  dark: {
    memory: '#ffd37e',
    confirmation: '#ffd37e',
    error: '#ff7d9d',
    muted: '#a6b6dc',
    disconnected: '#a6b6dc',
  },
};

export function orbStateColors(theme: ThemeName): OrbStateColors {
  return ORB_STATE_COLORS[theme];
}

/** Page background per theme (`--bg-app`), for contrast/fallback decisions. */
export const THEME_BACKGROUND: Record<ThemeName, string> = {
  light: '#eaf6ff',
  dark: '#080f29',
};
