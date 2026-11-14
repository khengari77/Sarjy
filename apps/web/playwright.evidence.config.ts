import { defineConfig } from '@playwright/test';
import { resolve } from 'node:path';
import base from './playwright.config';

const evidenceDir = process.env.SARJY_EVIDENCE_DIR
  ? resolve(process.env.SARJY_EVIDENCE_DIR)
  : resolve('test-results/evidence/current');

/**
 * Acceptance evidence is opt-in and serialized: it writes screenshots and
 * measured JSON under an ignored exact-SHA directory. It is deliberately not
 * part of `test:browser`, so a red evidence gate never blocks ordinary PR CI
 * on a base that is missing an unmerged feature.
 */
export default defineConfig({
  ...base,
  testDir: 'test/evidence',
  testMatch: '**/*.evidence.ts',
  outputDir: resolve(evidenceDir, 'playwright'),
  metadata: { sha: process.env.SARJY_EVIDENCE_SHA ?? '' },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [
    ['list', { printSteps: false }],
    ['json', { outputFile: resolve(evidenceDir, 'report.json') }],
  ],
});
