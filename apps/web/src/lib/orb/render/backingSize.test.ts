import { describe, expect, it } from 'vitest';
import { createBackingSizer, type CssSize } from './backingSize';

function fakeCanvas() {
  const writes: number[] = [];
  let width = 0;
  let height = 0;
  const canvas = {
    get width() {
      return width;
    },
    set width(value: number) {
      width = value;
      writes.push(value);
    },
    get height() {
      return height;
    },
    set height(value: number) {
      height = value;
      writes.push(value);
    },
  } as unknown as HTMLCanvasElement;
  return { canvas, writes };
}

describe('backing buffer sizing', () => {
  it('writes once, then stops when frames repeat at the same size and DPR', () => {
    const css: CssSize = { width: 300, height: 150 };
    const dpr = 2;
    const { canvas, writes } = fakeCanvas();
    const sizer = createBackingSizer(
      canvas,
      () => css,
      () => dpr,
      () => 2,
    );

    expect(sizer.apply()).toBe(true);
    expect(writes).toEqual([600, 300]);
    for (let index = 0; index < 5; index += 1) expect(sizer.apply()).toBe(false);
    expect(writes).toEqual([600, 300]);
  });

  it('updates for CSS size and DPR changes, but not repeated identical notifications', () => {
    let css: CssSize = { width: 300, height: 150 };
    let dpr = 1;
    const { canvas, writes } = fakeCanvas();
    const sizer = createBackingSizer(
      canvas,
      () => css,
      () => dpr,
      () => 2,
    );

    expect(sizer.refreshCss()).toBe(true);
    expect(writes).toEqual([300, 150]);

    dpr = 2;
    expect(sizer.apply()).toBe(true);
    expect(writes).toEqual([300, 150, 600, 300]);

    css = { width: 400, height: 200 };
    expect(sizer.refreshCss()).toBe(true);
    expect(writes).toEqual([300, 150, 600, 300, 800, 400]);

    css = { width: 400, height: 200 };
    expect(sizer.refreshCss()).toBe(false);
    expect(writes).toHaveLength(6);
  });

  it('caps DPR for small viewports and never writes after disposal', () => {
    let dpr = 3;
    const { canvas, writes } = fakeCanvas();
    const sizer = createBackingSizer(
      canvas,
      () => ({ width: 100, height: 100 }),
      () => dpr,
      () => 1.5,
    );

    expect(sizer.apply()).toBe(true);
    expect(writes).toEqual([150, 150]);

    sizer.dispose();
    dpr = 4;
    expect(sizer.apply()).toBe(false);
    expect(sizer.refreshCss()).toBe(false);
    expect(writes).toEqual([150, 150]);
  });
});
