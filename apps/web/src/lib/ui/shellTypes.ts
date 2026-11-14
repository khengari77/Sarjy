import type { Snippet } from 'svelte';
import type { ResolvedTheme } from './theme';

export interface ShellProps {
  dir?: 'ltr' | 'rtl';
  languageLabel?: string;
  onLanguage?: () => void;
  leftLabel?: string;
  rightLabel?: string;
  left?: Snippet;
  stage?: Snippet;
  /** Always-reachable control surface (call controls, composer). */
  controls?: Snippet;
  right?: Snippet;
  /** A dialog inside the stage has taken over modal ownership. */
  stageDialogOpen?: boolean;
  /** Panel state is shared with the command store so voice can open panels. */
  leftOpen?: boolean;
  rightOpen?: boolean;
  /**
   * Reports the resolved (not preferred) theme. The shell owns storage and
   * OS-preference resolution, so consumers such as the orb must not
   * re-derive it from `data-theme` or `prefers-color-scheme` themselves.
   */
  onthemechange?: (theme: ResolvedTheme) => void;
}
