import type { CDPSession, Page } from '@playwright/test';

/**
 * Instrumentation for the five-minute stability run.
 *
 * It counts live requestAnimationFrame callbacks, live observers, live audio
 * and WebGL contexts, DOM nodes, and (through CDP) DOM listeners and detached
 * nodes. Everything is sampled from the real browser; no value is inferred
 * from application code.
 */

interface ProfileState {
  activeRaf: number;
  rafSchedules: number;
  observers: number;
  observerObserves: number;
  audioContexts: number;
  audioContextsClosed: number;
  webglContexts: number;
  webglLost: number;
}

export async function installProfiler(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const state: ProfileState = {
      activeRaf: 0,
      rafSchedules: 0,
      observers: 0,
      observerObserves: 0,
      audioContexts: 0,
      audioContextsClosed: 0,
      webglContexts: 0,
      webglLost: 0,
    };
    (window as unknown as { __sarjyProfile: ProfileState }).__sarjyProfile = state;

    const pendingFrames = new Set<number>();
    const requestFrame = window.requestAnimationFrame.bind(window);
    window.requestAnimationFrame = (callback: FrameRequestCallback): number => {
      let handle = 0;
      handle = requestFrame((time) => {
        pendingFrames.delete(handle);
        try {
          callback(time);
        } finally {
          state.activeRaf = pendingFrames.size;
        }
      });
      pendingFrames.add(handle);
      state.activeRaf = pendingFrames.size;
      state.rafSchedules += 1;
      return handle;
    };
    const cancelFrame = window.cancelAnimationFrame.bind(window);
    window.cancelAnimationFrame = (handle: number): void => {
      cancelFrame(handle);
      pendingFrames.delete(handle);
      state.activeRaf = pendingFrames.size;
    };

    interface ObserverInstance {
      observe(...args: never[]): void;
      disconnect(): void;
    }
    type ObserverConstructor = new (callback: (...args: never[]) => void) => ObserverInstance;
    for (const name of ['MutationObserver', 'ResizeObserver', 'IntersectionObserver'] as const) {
      const Original = (window as unknown as Record<string, ObserverConstructor | undefined>)[name];
      if (!Original) continue;
      const Tracked = class extends Original {
        _trackedClosed = false;
        constructor(callback: (...args: never[]) => void) {
          super(callback);
          state.observers += 1;
        }
        observe(...args: never[]): void {
          state.observerObserves += 1;
          super.observe(...args);
        }
        disconnect(): void {
          if (!this._trackedClosed) {
            this._trackedClosed = true;
            state.observers -= 1;
          }
          super.disconnect();
        }
      };
      (window as unknown as Record<string, unknown>)[name] = Tracked;
    }

    type AudioConstructor = new (...args: never[]) => AudioContext;
    for (const name of ['AudioContext', 'webkitAudioContext'] as const) {
      const Original = (window as unknown as Record<string, AudioConstructor | undefined>)[name];
      if (!Original) continue;
      const Tracked = class extends Original {
        constructor(...args: never[]) {
          super(...args);
          state.audioContexts += 1;
          const close = this.close.bind(this);
          this.close = async (): Promise<void> => {
            state.audioContexts -= 1;
            state.audioContextsClosed += 1;
            return close();
          };
        }
      };
      (window as unknown as Record<string, unknown>)[name] = Tracked;
    }

    const trackedCanvases = new WeakSet<HTMLCanvasElement>();
    const originalGetContext = (
      HTMLCanvasElement.prototype as unknown as {
        getContext: (...args: unknown[]) => unknown;
      }
    ).getContext;
    (HTMLCanvasElement.prototype as unknown as { getContext: (...args: unknown[]) => unknown }).getContext = function (
      this: HTMLCanvasElement,
      ...args: unknown[]
    ) {
      const context = originalGetContext.apply(this, args);
      if (context && String(args[0]).startsWith('webgl') && !trackedCanvases.has(this)) {
        trackedCanvases.add(this);
        state.webglContexts += 1;
        this.addEventListener('webglcontextlost', () => {
          state.webglLost += 1;
        });
      }
      return context;
    };
  });
}

export interface ResourceSnapshot {
  elapsedSeconds: number;
  activeRaf: number;
  rafSchedules: number;
  observers: number;
  observerObserves: number;
  audioContexts: number;
  audioContextsClosed: number;
  webglContexts: number;
  webglLost: number;
  nodes: number;
  jsEventListeners: number | null;
  heapUsedBytes: number | null;
  detachedNodes: number | null;
}

export async function readSnapshot(page: Page, session: CDPSession, elapsedSeconds: number): Promise<ResourceSnapshot> {
  const browserSide = await page.evaluate(() => {
    const profile = (window as unknown as { __sarjyProfile?: ProfileState }).__sarjyProfile;
    const performanceWithMemory = performance as Performance & { memory?: { usedJSHeapSize: number } };
    return {
      activeRaf: profile?.activeRaf ?? 0,
      rafSchedules: profile?.rafSchedules ?? 0,
      observers: profile?.observers ?? 0,
      observerObserves: profile?.observerObserves ?? 0,
      audioContexts: profile?.audioContexts ?? 0,
      audioContextsClosed: profile?.audioContextsClosed ?? 0,
      webglContexts: profile?.webglContexts ?? 0,
      webglLost: profile?.webglLost ?? 0,
      nodes: document.querySelectorAll('*').length,
      heapUsedBytes: performanceWithMemory.memory?.usedJSHeapSize ?? null,
    };
  });

  const counters = await readDomCounters(session);
  const detachedNodes = await countDetachedNodes(session);
  return {
    elapsedSeconds,
    ...browserSide,
    jsEventListeners: counters?.jsEventListeners ?? null,
    detachedNodes,
  };
}

export async function openCdpSession(page: Page): Promise<CDPSession> {
  const session = await page.context().newCDPSession(page);
  // queryObjects needs an enabled Runtime domain in this Chromium build.
  await session.send('Runtime.enable').catch(() => undefined);
  return session;
}

interface DomCounters {
  documents: number;
  nodes: number;
  jsEventListeners: number;
}

async function readDomCounters(session: CDPSession): Promise<DomCounters | null> {
  try {
    return (await session.send('Memory.getDOMCounters')) as DomCounters;
  } catch {
    return null;
  }
}

async function countDetachedNodes(session: CDPSession): Promise<number | null> {
  try {
    await session.send('DOM.enable');
    const detached = await session.send('DOM.getDetachedDomNodes');
    const nodes = (detached as { detachedNodes?: unknown[] }).detachedNodes;
    if (Array.isArray(nodes)) return nodes.length;
  } catch {
    // Fall through to the object query below.
  }
  try {
    const prototype = await session.send('Runtime.evaluate', { expression: 'Node.prototype' });
    if (!prototype.result.objectId) return null;
    const objects = await session.send('Runtime.queryObjects', {
      prototypeObjectId: prototype.result.objectId,
    });
    const counted = await session.send('Runtime.callFunctionOn', {
      objectId: objects.objects.objectId,
      functionDeclaration:
        'function () { return Array.from(this).filter(function (node) { return node && node.isConnected === false; }).length; }',
      returnByValue: true,
    });
    return typeof counted.result.value === 'number' ? counted.result.value : null;
  } catch {
    return null;
  }
}

export interface ResourceReport {
  sha: string;
  mode: 'idle' | 'listening';
  durationSeconds: number;
  sampleIntervalSeconds: number;
  environment: {
    userAgent: string;
    browserVersion: string;
    hardwareConcurrency: number;
    deviceMemoryGb: number | null;
    viewport: { width: number; height: number };
    headless: boolean;
  };
  samples: ResourceSnapshot[];
  growth: Record<string, number>;
  tolerances: Record<string, number>;
  violations: string[];
  ok: boolean;
  limitations: string[];
}

export function analyseSamples(
  samples: ResourceSnapshot[],
): Pick<ResourceReport, 'growth' | 'tolerances' | 'violations' | 'ok'> {
  const first = samples[0];
  const last = samples[samples.length - 1];
  const growth = {
    activeRaf: last.activeRaf - first.activeRaf,
    observers: last.observers - first.observers,
    audioContexts: last.audioContexts - first.audioContexts,
    webglContexts: last.webglContexts - first.webglContexts,
    nodes: last.nodes - first.nodes,
    jsEventListeners: (last.jsEventListeners ?? 0) - (first.jsEventListeners ?? 0),
    detachedNodes: (last.detachedNodes ?? 0) - (first.detachedNodes ?? 0),
  };
  const tolerances = {
    activeRaf: 2,
    observers: 2,
    audioContexts: 0,
    webglContexts: 0,
    nodes: Math.max(50, Math.ceil(first.nodes * 0.05)),
    jsEventListeners: 50,
    detachedNodes: 25,
  };
  const violations = Object.entries(growth)
    .filter(([key, value]) => value > tolerances[key as keyof typeof tolerances])
    .map(([key, value]) => `${key} grew by ${value} (tolerance ${tolerances[key as keyof typeof tolerances]})`);
  return { growth, tolerances, violations, ok: violations.length === 0 };
}
