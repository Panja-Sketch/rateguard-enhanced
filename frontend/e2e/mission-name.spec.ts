import { expect, test, type Page } from '@playwright/test';
import { installMocks, signIn } from './support';

const MARKED = '[CANDIDATE-VERIFY-babc5cb90952] controlled workbook vs versioned REST connector';
const HOSTILE = '<img src=x onerror="window.__pwned=1"> <script>window.__pwned=1</script>';

/** Captures the body of POST /api/v1/missions and answers 202 (the real API is never contacted). */
async function captureMissionCreate(page: Page): Promise<{ bodies: Array<Record<string, unknown>> }> {
  const bodies: Array<Record<string, unknown>> = [];
  await page.route(/\/api\/v1\/missions$/, async (route) => {
    const req = route.request();
    if (req.method() !== 'POST') return route.fallback();
    bodies.push(JSON.parse(req.postData() || '{}'));
    return route.fulfill({
      status: 202,
      contentType: 'application/json',
      headers: { 'access-control-allow-origin': 'http://localhost:3100' },
      body: JSON.stringify({ mission_id: 'MIS-E2ENAME1', status: 'QUEUED', mode: 'RELEASE_CONFORMANCE', decision: '', result: {} }),
    });
  });
  return { bodies };
}

async function openSourcesWithDemoSample(page: Page) {
  await page.goto('/sources');
  await signIn(page);
  await page.getByText('Use demo sample').click();
}

const missionDetail = (name: string) => ({
  mission_id: 'MIS-E2ENAME1',
  status: 'COMPLETED',
  decision: 'BLOCK_DEPLOYMENT',
  summary: 's',
  updated_at: new Date().toISOString(),
  metadata: { name, mode: 'RELEASE_CONFORMANCE' },
  eligible_actions: { cancel: false, retry: false, archive: false, delete: false },
  result: { mission_id: 'MIS-E2ENAME1', overall_status: 'COMPLETED', ai_runtime: {} },
});

test.describe('mission name', () => {
  test('the Sources launcher sends the entered name, trimmed', async ({ page }) => {
    await installMocks(page, { role: 'RELEASE_OWNER', extraGet: { '/api/v1/missions/MIS-E2ENAME1': missionDetail(MARKED) } });
    const { bodies } = await captureMissionCreate(page);
    await openSourcesWithDemoSample(page);
    await page.getByTestId('mission-name-input').fill(`   ${MARKED}   `);
    await page.getByRole('button', { name: /Execute Assurance/ }).click();
    await expect.poll(() => bodies.length).toBe(1);
    expect(bodies[0].name).toBe(MARKED);
  });

  test('a blank name keeps the legacy default', async ({ page }) => {
    await installMocks(page, { role: 'RELEASE_OWNER', extraGet: { '/api/v1/missions/MIS-E2ENAME1': missionDetail('x') } });
    const { bodies } = await captureMissionCreate(page);
    await openSourcesWithDemoSample(page);
    await page.getByTestId('mission-name-input').fill('    ');
    await page.getByRole('button', { name: /Execute Assurance/ }).click();
    await expect.poll(() => bodies.length).toBe(1);
    expect(bodies[0].name).toBe('Assurance Mission Launched from Sources');
  });

  test('an invalid name is refused in the browser and nothing is sent', async ({ page }) => {
    await installMocks(page, { role: 'RELEASE_OWNER' });
    const { bodies } = await captureMissionCreate(page);
    await openSourcesWithDemoSample(page);
    await page.getByTestId('mission-name-input').fill('bad​name');
    await page.getByRole('button', { name: /Execute Assurance/ }).click();
    await expect(page.locator('body')).toContainText('control characters');
    expect(bodies).toHaveLength(0);
  });

  test('the launcher documents the verification format without a hardcoded SHA', async ({ page }) => {
    await installMocks(page, { role: 'RELEASE_OWNER' });
    await page.goto('/sources');
    await signIn(page);
    await expect(page.locator('body')).toContainText('[CANDIDATE-VERIFY-<first 12 characters of the commit SHA>]');
  });

  test('mission history and detail render a hostile name as inert text', async ({ page }) => {
    const listItem = {
      mission_id: 'MIS-E2ENAME1', name: HOSTILE, mode: 'RELEASE_CONFORMANCE', status: 'COMPLETED', decision: 'BLOCK_DEPLOYMENT',
      updated_at: new Date().toISOString(), created_at: new Date().toISOString(),
      eligible_actions: { cancel: false, retry: false, archive: false, delete: false },
    };
    await installMocks(page, {
      role: 'RELEASE_OWNER',
      extraGet: {
        '/api/v1/missions': { missions: [listItem], total_count: 1, limit: 50, offset: 0 },
        '/api/v1/missions/MIS-E2ENAME1': missionDetail(HOSTILE),
        '/api/v1/assurance/runs/MIS-E2ENAME1/events': { events: [] },
        '/api/v1/assurance/runs/MIS-E2ENAME1/evidence': { evidence: [] },
        '/api/v1/missions/MIS-E2ENAME1/connector-evidence': { mission_id: 'MIS-E2ENAME1', connector_invocation_count: 0, connector_invocations: [] },
      },
    });
    await page.goto('/missions');
    await signIn(page);
    await expect(page.locator('body')).toContainText('<img src=x onerror=');
    await page.goto('/missions/MIS-E2ENAME1');
    await expect(page.locator('body')).toContainText('<img src=x onerror=');
    expect(await page.locator('img[src="x"]').count()).toBe(0);
    expect(await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned)).toBeUndefined();
  });
});
