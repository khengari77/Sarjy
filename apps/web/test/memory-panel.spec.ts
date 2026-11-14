import { expect, test } from '@playwright/test';
import { mockApi, openMemoryTab, waitForShell } from './memory-harness';

test.beforeEach(async ({ page }) => {
  test.setTimeout(90_000);
  page.setDefaultTimeout(20_000);
  await page.addInitScript(() => localStorage.setItem('sarjy-language', 'en'));
});

// Pin the OS scheme so "system" resolves predictably when cycling themes here.
test.use({ colorScheme: 'light' });

test('the memory explorer mounts in the drawer, scopes the list to the conversation and shows inclusion', async ({
  page,
}) => {
  const calls = await mockApi(page);
  await page.addInitScript(() => localStorage.setItem('sarjy-theme', 'dark'));
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto('/');
  await waitForShell(page);

  // Selecting a conversation is what gives the panel its conversation scope.
  await page.locator('#shell-left').getByRole('button', { name: 'Trip to Paris', exact: true }).click();
  const panel = await openMemoryTab(page);

  await expect(panel).toBeVisible();
  await expect(panel).toContainText('Prefers tea over coffee');
  await expect(panel).toContainText('Lives in Riyadh');
  await expect(panel).toContainText('4 shown of 4 stored facts');

  // The inclusion projection is the acceptance point: one fact is in the next
  // compiled context, one is already excluded from it.
  // The panel must call this candidacy, not inclusion: the compiler can still
  // trim the prompt to its character budget.
  await expect(panel.locator('[data-included="true"]')).toContainText('Candidate for the next compiled context');
  const excludedRow = panel.locator('li.memory-row', { hasText: 'Lives in Riyadh' }).first();
  await expect(excludedRow).toContainText('Excluded from this conversation');
  await expect(panel.locator('[data-included="true"]')).toContainText('Candidate');
  await expect(excludedRow).toContainText('Excluded');
  await expect(panel).not.toContainText('Included in the next compiled context');

  // A superseded row must not offer a control the API refuses (it requires
  // status == "active"), otherwise excluding it 404s while the row is on screen.
  const staleRow = panel.locator('li.memory-row', { hasText: 'Lives in Riyadh (corrected)' });
  await expect(staleRow.getByRole('button', { name: 'Exclude…' })).toBeDisabled();
  await expect(panel.locator('[data-included="true"]').getByRole('button', { name: 'Exclude…' })).toBeEnabled();

  // A fact excluded through correction lineage is still excluded by the
  // compiler. Offering "Exclude…" here is what let a confirm+undo claim the
  // descendant was eligible while the next request kept omitting it.
  const lineageRow = panel.locator('li.memory-row', { hasText: 'Hotel in Paris, 2 nights' });
  const lineageExclude = lineageRow.getByRole('button', { name: 'Exclude…' });
  await expect(lineageExclude).toBeDisabled();
  await expect(lineageExclude).toHaveAttribute('title', /Already excluded from this conversation/);
  await expect(lineageRow).toContainText('Excluded from this conversation');

  // Owner scoping is enforced by the API; the UI must at least ask for the
  // selected conversation rather than an unscoped dump.
  const scoped = calls.memories.filter((url) => new URL(url).searchParams.get('conversation_id') === 'c1');
  expect(scoped.length).toBeGreaterThan(0);

  // Reachable and legible in both themes without remounting the shell.
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect(panel).toBeVisible();
  // dark → system, which resolves to light under the pinned colour scheme.
  await page.getByRole('button', { name: /^Theme:/ }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect(panel).toBeVisible();
  await expect(panel).toContainText('Prefers tea over coffee');
});

test('excluding previews without writing, then confirms and undoes through the server', async ({ page }) => {
  const calls = await mockApi(page);
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto('/');
  await waitForShell(page);

  await page.locator('#shell-left').getByRole('button', { name: 'Trip to Paris', exact: true }).click();
  const panel = await openMemoryTab(page);
  await expect(panel).toContainText('Prefers tea over coffee');

  const includedRow = panel.locator('[data-included="true"]');
  await includedRow.getByRole('button', { name: 'Exclude…' }).click();

  // Preview only: the review exists, says nothing changed, and no confirm was sent.
  const review = panel.getByTestId('exclusion-review');
  await expect(review).toBeVisible();
  await expect(review).toContainText('Preview only — nothing has changed yet');
  await expect(review).toContainText('Will be excluded from this conversation');
  await expect(review).toContainText('Revision 3 → 4');
  expect(calls.previews).toBe(1);
  expect(calls.confirms).toHaveLength(0);
  // The stored row is unchanged while the preview is open.
  await expect(includedRow).toContainText('Candidate for the next compiled context');
  await expect(panel.getByTestId('exclusion-confirmed')).toHaveCount(0);

  await review.getByRole('button', { name: 'Confirm exclusion' }).click();

  await expect(panel.getByTestId('exclusion-confirmed')).toContainText('Exclusion confirmed');
  await expect(panel.getByTestId('exclusion-confirmed')).toContainText('Applied change');
  expect(calls.confirms).toHaveLength(1);
  expect(calls.confirms[0].proposal).toBe('signed-preview-token');
  expect(calls.confirms[0].expected_revision).toBe(3);
  await expect(panel.getByTestId('exclusion-review')).toHaveCount(0);

  await panel.getByRole('button', { name: 'Undo exclusion' }).click();
  await expect(panel.getByTestId('exclusion-undone')).toContainText('Exclusion undone');
  expect(calls.undos).toHaveLength(1);
  expect(calls.undos[0].operation_id).toBe('op-1');
  expect(calls.undos[0].expected_revision).toBe(4);
});
