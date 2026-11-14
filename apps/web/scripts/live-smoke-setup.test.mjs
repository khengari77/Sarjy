import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
const script = fileURLToPath(new URL('./live-smoke.mjs', import.meta.url));
for (const args of [[], ['--locale=en']]) {
  test(`offline smoke setup ${args.join(' ') || 'default'}`, () => {
    const result = spawnSync(process.execPath, [script, '--check-options', ...args], {
      encoding: 'utf8',
      timeout: 10000,
      env: { ...process.env, PLAYWRIGHT_BROWSERS_PATH: '/nonexistent-offline-smoke-browser' },
    });
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(JSON.parse(result.stdout), { locale: 'en', callLabel: 'Start call', voiceLabel: 'Choose voice' });
  });
}
test('unsupported locale fails before browser or provider access', () => {
  const result = spawnSync(process.execPath, [script, '--check-options', '--locale=ar'], {
    encoding: 'utf8',
    timeout: 10000,
  });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /UNSUPPORTED_SMOKE_LOCALE/);
});
test('microphone fails before browser access even with --check-options', () => {
  const result = spawnSync(process.execPath, [script, '--check-options', '--microphone'], {
    encoding: 'utf8',
    timeout: 10000,
    env: { ...process.env, PLAYWRIGHT_BROWSERS_PATH: '/nonexistent-offline-smoke-browser' },
  });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /ENGLISH_MICROPHONE_FIXTURE_REQUIRED/);
});
test('live smoke contains no non-English selectors or copy', () => {
  // The English UI legitimately uses these typographic characters in accessible
  // names and copy; every other non-ASCII character means a stale locale string.
  const allowed = new Set(['…', '↗', '·', '→']);
  const offenders = readFileSync(script, 'utf8')
    .split('\n')
    .flatMap((line, index) =>
      Array.from(line)
        .filter((character) => character.codePointAt(0) > 0x7f && !allowed.has(character))
        .map(
          (character) =>
            `line ${index + 1}: U+${character.codePointAt(0).toString(16).toUpperCase()} in ${line.trim()}`,
        ),
    );
  assert.deepEqual(offenders, []);
});
