import type { Snippet } from 'svelte';

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
}
