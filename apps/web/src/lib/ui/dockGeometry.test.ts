import { describe, expect, it } from 'vitest';
import { publishDockGeometry } from './dockGeometry';

interface FakeRoot {
  root: HTMLElement;
  values: Map<string, string>;
  dataset: DOMStringMap;
}

function fakeRoot(): FakeRoot {
  const values = new Map<string, string>();
  const element = {
    style: {
      setProperty: (name: string, value: string) => values.set(name, value),
      removeProperty: (name: string) => values.delete(name),
    },
    dataset: {} as DOMStringMap,
  };
  return { root: element as unknown as HTMLElement, values, dataset: element.dataset };
}

function rect(top: number, height: number, width = 1000): DOMRect {
  return { top, height, left: 0, width } as DOMRect;
}

const C = Number.parseFloat;

describe('publishDockGeometry', () => {
  it('sits 12px above a roomy desktop dock', () => {
    const { root, values, dataset } = fakeRoot();
    expect(publishDockGeometry(root, rect(700, 150), rect(0, 900), null, 900)).toBe(true);
    expect(dataset.toastReady).toBe('true');
    const top = C(values.get('--toast-top')!);
    const bottom = C(values.get('--toast-bottom')!);
    expect(top + bottom).toBeLessThanOrEqual(900);
    expect(900 - bottom).toBeLessThanOrEqual(700 - 12);
  });

  it('hides when the dock leaves less than 40px of band (320x256, dockTop 108)', () => {
    const { root, dataset } = fakeRoot();
    expect(publishDockGeometry(root, rect(108, 150), rect(0, 256), null, 256)).toBe(false);
    expect(dataset.toastReady).toBeUndefined();
  });

  it('hides when the dock is above the topbar (320x200, dockTop 52)', () => {
    const { root, dataset } = fakeRoot();
    expect(publishDockGeometry(root, rect(52, 150), rect(0, 200), null, 200)).toBe(false);
    expect(dataset.toastReady).toBeUndefined();
  });

  it('uses the viewport bottom edge when the dock is off the bottom', () => {
    const { root, values, dataset } = fakeRoot();
    expect(publishDockGeometry(root, rect(300, 150), rect(0, 256), null, 256)).toBe(true);
    expect(dataset.toastReady).toBe('true');
    expect(values.get('--toast-bottom')).toBe('12px');
  });

  it('hides for a zero-size dock', () => {
    const { root, dataset } = fakeRoot();
    expect(publishDockGeometry(root, rect(0, 0), rect(0, 900), null, 900)).toBe(false);
    expect(dataset.toastReady).toBeUndefined();
  });

  it('never overlaps the dock gap when visible', () => {
    for (const height of [256, 320, 900]) {
      for (const dockTop of [120, 144, 200, 300]) {
        if (dockTop >= height) continue;
        const { root, values, dataset } = fakeRoot();
        if (!publishDockGeometry(root, rect(dockTop, 120), rect(0, height), null, height)) {
          expect(dataset.toastReady).toBeUndefined();
          continue;
        }
        const bottom = C(values.get('--toast-bottom')!);
        expect(height - bottom, `layer bottom at dockTop ${dockTop}/${height}`).toBeLessThanOrEqual(dockTop - 12);
      }
    }
  });
});
