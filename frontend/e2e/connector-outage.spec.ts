import { expect, test } from '@playwright/test';
import { installMocks, signIn } from './support';

const MISSION = 'MIS-E2EOUT01';

const experiments = Array.from({ length: 11 }, (_, i) => ({
  experiment_id: `RG-${i}`,
  probe_name: `probe ${i}`,
  category: 'RISK_DIRECTED',
  risk_inputs: { roof_age: i },
  expected_premium: '600.00',
  actual_premium: 'N/A (connector failure or partial response)',
  matches: false,
  outcome: 'INCONCLUSIVE',
  inconclusive_reason: 'CONNECTOR_FAILURE',
  inconclusive_class: 'CONNECTOR_AUTH_DENIED',
}));

const outageMission = {
  mission_id: MISSION,
  status: 'COMPLETED',
  decision: 'REVIEW_REQUIRED',
  summary: 's',
  updated_at: new Date().toISOString(),
  metadata: { name: 'E2E outage mission', mode: 'RELEASE_CONFORMANCE' },
  eligible_actions: { cancel: false, retry: false, archive: false, delete: false },
  result: {
    mission_id: MISSION,
    overall_status: 'COMPLETED',
    ai_runtime: { model_status: 'NOT_INVOKED_DETERMINISTIC_PIPELINE' },
    experiments: {
      status: 'SUCCEEDED',
      data: { total_generated: 11, total_executed: 11, match_count: 0, mismatch_count: 0, inconclusive_count: 11, reduction_pct: 0, experiments },
    },
    release_decision: {
      status: 'SUCCEEDED',
      data: { status: 'REVIEW_REQUIRED', summary: 'review', blocking_reasons: [], recommendation: 'restore the connector' },
    },
  },
};

test('an all-inconclusive connector outage never says zero diffs were found', async ({ page }) => {
  await installMocks(page, {
    role: 'RELEASE_OWNER',
    extraGet: {
      [`/api/v1/missions/${MISSION}`]: outageMission,
      [`/api/v1/assurance/runs/${MISSION}/events`]: { events: [] },
      [`/api/v1/assurance/runs/${MISSION}/evidence`]: { evidence: [] },
      [`/api/v1/missions/${MISSION}/connector-evidence`]: { mission_id: MISSION, connector_invocation_count: 0, connector_invocations: [] },
    },
  });
  await page.goto(`/missions/${MISSION}`);
  await signIn(page);
  const body = page.locator('body');
  await expect(body).toContainText('No valid premium comparisons were completed because the connector was unavailable');
  await expect(body).toContainText('the connector rejected the service identity');
  await expect(body).not.toContainText(/zero diffs/i);
});
