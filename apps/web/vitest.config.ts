import { svelte } from '@sveltejs/vite-plugin-svelte';
import { defineConfig } from 'vitest/config';

// The svelte plugin compiles runes in `*.svelte.ts` modules (the memory
// explorer's state class) so their methods can be unit-tested.
export default defineConfig({
  plugins: [svelte({ hot: false })],
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
});
