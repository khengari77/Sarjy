#!/usr/bin/env node
/**
 * Build a human-readable evidence index from Playwright's JSON report.
 *
 * Usage: node apps/web/scripts/evidence-index.mjs [artifact-dir]
 * The directory holds report.json, screens/, and measured JSON files. Large
 * binaries stay in the ignored artifact directory and are linked, not copied.
 */
import { readFile, readdir, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';

const directory = resolve(process.argv[2] ?? 'test-results/evidence/current');

const PENDING_DEPENDENCIES = [
  ['Theme control, persistence, Escape and focus return', '#120', 'agent/issue-37-foundation'],
  ['Theme-aware orb derivation and state contrast', '#121', 'agent/issue-38-state'],
  ['Filled circular cloudy orb and runtime phases', '#123', 'agent/issue-40-orb'],
  ['Captions, transcript drawer and history states', '#119', 'agent/issue-41-chat'],
  ['Default approved voice, one-tap start', '#117', 'agent/issue-44-default-voice'],
  ['Transparent memory explorer', '#122', 'agent/issue-43-memory'],
  ['Resumable onboarding', '#126', 'agent/issue-111-onboarding'],
  ['ACKed mid-session voice switch', '#124', 'agent/issue-112-voice-switch'],
  ['Wordle stage UI', '#125', 'agent/issue-109-wordle-ui'],
  ['Wordle data ingestion and game API', '#118', 'agent/issue-108-wordle'],
  ['Read-only Simple/Developer mode', '#127', 'agent/issue-46-dev-mode'],
];

function collect(suites, output) {
  for (const suite of suites ?? []) {
    for (const spec of suite.specs ?? []) {
      const results = (spec.tests ?? []).flatMap((entry) => entry.results ?? []);
      const statuses = results.map((result) => result.status);
      const annotations = (spec.tests ?? []).flatMap((entry) => entry.annotations ?? []);
      const duration = results.reduce((total, result) => total + (result.duration ?? 0), 0);
      let status = 'passed';
      if (statuses.length && statuses.every((value) => value === 'skipped')) status = 'skipped';
      else if (statuses.some((value) => value === 'failed' || value === 'timedOut')) status = 'failed';
      output.push({
        title: spec.title,
        status,
        durationMs: duration,
        annotations: annotations.map((annotation) => `${annotation.type}: ${annotation.description ?? ''}`),
        errors: results
          .flatMap((result) => result.errors ?? [])
          .map((error) => (error.message ?? '').split('\n')[0])
          .filter(Boolean),
      });
    }
    collect(suite.suites, output);
  }
}

async function listFiles(root, prefix = '') {
  const entries = await readdir(root, { withFileTypes: true }).catch(() => []);
  const files = [];
  for (const entry of entries) {
    const path = join(root, entry.name);
    const relativePath = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.isDirectory()) files.push(...(await listFiles(path, relativePath)));
    else files.push(relativePath);
  }
  return files;
}

const reportPath = join(directory, 'report.json');
const report = JSON.parse(await readFile(reportPath, 'utf8').catch(() => '{"suites":[]}'));
const tests = [];
collect(report.suites, tests);

const counts = {
  passed: tests.filter((entry) => entry.status === 'passed').length,
  failed: tests.filter((entry) => entry.status === 'failed').length,
  skipped: tests.filter((entry) => entry.status === 'skipped').length,
};
const screenshots = (await listFiles(directory)).filter((file) => file.endsWith('.png')).sort();
const measurements = (await listFiles(directory)).filter(
  (file) => file.endsWith('.json') && file !== 'report.json' && file !== 'summary.json',
);
const sha = report.config?.metadata?.sha ?? process.env.SARJY_EVIDENCE_SHA ?? report.suites?.[0]?.title ?? 'unknown';
const generatedAt = new Date().toISOString();
const command = process.env.SARJY_EVIDENCE_COMMAND ?? 'bash scripts/run_evidence.sh';

const lines = [
  `# Sarjy acceptance evidence — ${sha}`,
  '',
  `Generated: ${generatedAt}`,
  '',
  `Command: \`${command}\``,
  '',
  `Result: **${counts.passed} passed, ${counts.failed} failed, ${counts.skipped} skipped**`,
  '',
  'This index is generated from the Playwright JSON report. Screenshots and measurements live in the ignored',
  'artifact directory; they are referenced here, not committed. A skipped or failed row is not acceptance.',
  '',
  '## Test results',
  '',
  '| Test | Status | Duration | Notes |',
  '| --- | --- | --- | --- |',
  ...tests.map((entry) => {
    const notes = [...entry.annotations, ...entry.errors].join('; ').replaceAll('|', '\\|');
    return `| ${entry.title.replaceAll('|', '\\|')} | ${entry.status} | ${Math.round(entry.durationMs)} ms | ${notes || ''} |`;
  }),
  '',
  '## Screenshots',
  '',
  ...(screenshots.length ? screenshots.map((file) => `- [${file}](${file})`) : ['- none captured']),
  '',
  '## Measurements',
  '',
  ...(measurements.length ? measurements.map((file) => `- [${file}](${file})`) : ['- none captured']),
  '',
  '## Skipped / unrun reasons',
  '',
  ...(tests.filter((entry) => entry.status === 'skipped').length
    ? tests
        .filter((entry) => entry.status === 'skipped')
        .map((entry) => `- **${entry.title}**: ${[...entry.annotations, ...entry.errors].join('; ')}`)
    : ['- none']),
  '',
  '## Pending feature dependencies (not claims)',
  '',
  '| Gate | Issue / PR | Branch |',
  '| --- | --- | --- |',
  ...PENDING_DEPENDENCIES.map(([gate, issue, branch]) => `| ${gate} | ${issue} | \`${branch}\` |`),
  '',
];

await writeFile(join(directory, 'index.md'), `${lines.join('\n')}\n`, 'utf8');
await writeFile(
  join(directory, 'summary.json'),
  `${JSON.stringify({ sha, generatedAt, command, counts, tests, screenshots, measurements }, null, 2)}\n`,
  'utf8',
);

console.log(`Evidence index: ${join(directory, 'index.md')}`);
console.log(`Result: ${counts.passed} passed, ${counts.failed} failed, ${counts.skipped} skipped`);
if (counts.failed > 0) console.log('Failed gates are recorded above; this run is NOT acceptance.');
