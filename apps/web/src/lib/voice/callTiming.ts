/**
 * Cheap call-start marks for measuring where a call spends its time.
 *
 * This is measurement only: every failure is swallowed so timing can never
 * change how a call behaves. Nothing is recorded unless this is a dev build or
 * the owner opted in with `localStorage.setItem('sarjy:call-timing', '1')`.
 */

import { browserStorage } from '../storage';

const TIMING_FLAG = 'sarjy:call-timing';

export interface CallTimingReport {
  marks: Record<string, number>;
  steps: Record<string, number>;
}

export interface CallTimer {
  readonly enabled: boolean;
  /** Record one instant; values are milliseconds since the first mark. First occurrence wins. */
  mark(name: string): void;
  /** Emit and return the report once; later calls (and marks after it) are ignored. */
  report(): CallTimingReport | null;
}

function timingEnabled(): boolean {
  try {
    if (import.meta.env.DEV) return true;
  } catch {
    /* no build environment available */
  }
  return browserStorage().getItem(TIMING_FLAG) === '1';
}

function emitReport(report: CallTimingReport): void {
  const payload = { ...report.marks, steps: report.steps };
  console.info('[Sarjy call-start]', payload);
  try {
    (window as unknown as { __sarjyCallStart?: typeof payload }).__sarjyCallStart = payload;
  } catch {
    /* window is unavailable in tests and non-browser execution */
  }
}

export function createCallTimer(now: () => number = () => performance.now()): CallTimer {
  if (!timingEnabled()) {
    return { enabled: false, mark() {}, report: () => null };
  }

  let anchor: number | null = null;
  let finished = false;
  const marks = new Map<string, number>();

  return {
    enabled: true,
    mark(name) {
      try {
        if (finished || marks.has(name)) return;
        const at = now();
        if (anchor === null) anchor = at;
        marks.set(name, at - anchor);
      } catch {
        /* timing is best effort */
      }
    },
    report() {
      try {
        if (finished || anchor === null) return null;
        finished = true;
        const values: Record<string, number> = {};
        const steps: Record<string, number> = {};
        let previous = 0;
        for (const [name, at] of marks) {
          const value = Math.max(0, Math.round(at));
          values[name] = value;
          steps[name] = Math.max(0, value - previous);
          previous = value;
        }
        const result = { marks: values, steps };
        emitReport(result);
        return result;
      } catch {
        return null;
      }
    },
  };
}
