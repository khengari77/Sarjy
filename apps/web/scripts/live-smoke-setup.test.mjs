import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
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
