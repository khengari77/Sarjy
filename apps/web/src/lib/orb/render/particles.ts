export function hexToRgb(hex: string): [number, number, number] {
  const value = hex.replace('#', '');
  const parsed = Number.parseInt(value.length === 3 ? value.replace(/(.)/g, '$1$1') : value, 16);
  return [((parsed >> 16) & 255) / 255, ((parsed >> 8) & 255) / 255, (parsed & 255) / 255];
}

/** Fibonacci sphere with a little radial jitter so the shell reads as organic. */
export function buildParticles(count: number): { positions: Float32Array; seeds: Float32Array } {
  const positions = new Float32Array(count * 3);
  const seeds = new Float32Array(count);
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let index = 0; index < count; index += 1) {
    const y = 1 - (2 * (index + 0.5)) / count;
    const radius = Math.sqrt(Math.max(0, 1 - y * y));
    const theta = golden * index;
    const shell = 0.9 + Math.random() * 0.16;
    positions[index * 3] = Math.cos(theta) * radius * shell;
    positions[index * 3 + 1] = y * shell;
    positions[index * 3 + 2] = Math.sin(theta) * radius * shell;
    seeds[index] = Math.random();
  }
  return { positions, seeds };
}
