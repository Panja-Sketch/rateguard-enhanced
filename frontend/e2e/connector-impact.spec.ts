import { expect, test } from '@playwright/test';
import { installMocks, signIn } from './support';

const MISSION = 'MIS-E2E0001';

const impact = {
  mission_id: MISSION,
  status: 'PARTIAL',
  job_id: 'IJ-e2e',
  progress: { batches_total: 250, batches_done: 240, batches_incomplete: 10, rows_processed: 48000, rows_in_scope: 50000, rows_total: 50000, mismatches_so_far: 900, inconclusive_so_far: 400, retries: 12, elapsed_seconds: 300 },
  connector: { connector_id: 'rating-engine-demo', engine_version: 'defective-v1', batch_quote: true },
  aggregate: {
    status: 'PARTIAL', impact_decision: 'BLOCK', completeness: 'PARTIAL',
    incomplete_reasons: ['INCONCLUSIVE_ROWS: 400 rows could not be compared.'],
    exposure_is_lower_bound: true, rows_total: 50000, processed_policies: 48000, eligible_policies: 37533,
    out_of_scope_policies: 12467, out_of_scope_reasons: { OUTSIDE_EFFECTIVE_PERIOD: 12467 },
    successful_comparisons: 37133, mismatches: 900, inconclusive: 400, unprocessed_policies: 0,
    coverage_pct: 98.93, affected_pct: 2.4, overcharge_count: 0, overcharge_total: '0.00',
    undercharge_count: 900, undercharge_total: '40500.00', signed_net_delta: '-40500.00', absolute_exposure: '40500.00',
    mean_abs_delta: '45.00', median_abs_delta: '45.00', min_delta: '-45.00', max_delta: '-45.00',
    batch_count: 250, batches_done: 240, batches_incomplete: 10, retry_count: 12, request_count: 262,
    error_classes: { CONNECTOR_ITEM_TEMPORARILY_UNAVAILABLE: 400 }, budget_exhausted: [], halt_reason: null,
    cohort_distribution: { minimum_cohort_size: 30, disclaimer: 'Screening only.', cohorts: [] },
    pipeline_impact: { as_of_date: '2026-09-20', total_affected_renewals_next_90_days: 3, buckets: [{ window_label: '0-30', affected_renewal_count: 3, total_absolute_impact: '135.00' }] },
    provenance: {}, result_sha256: 'abc',
  },
};

function mission(status: string) {
  return {
    mission_id: MISSION, status, decision: 'BLOCK_DEPLOYMENT', summary: 's', updated_at: new Date().toISOString(),
    metadata: { name: 'E2E impact mission', mode: 'RELEASE_CONFORMANCE' },
    eligible_actions: { cancel: false, retry: false, archive: false, delete: false },
    result: { mission_id: MISSION, overall_status: 'COMPLETED', ai_runtime: {}, release_decision: { status: 'SUCCEEDED', data: { status: 'BLOCK_DEPLOYMENT', summary: 'blocked', blocking_reasons: [], recommendation: 'fix' } } },
  };
}

const extra = {
  [`/api/v1/missions/${MISSION}`]: mission('COMPLETED'),
  [`/api/v1/missions/${MISSION}/impact`]: impact,
  [`/api/v1/assurance/runs/${MISSION}/events`]: { events: [] },
  [`/api/v1/assurance/runs/${MISSION}/evidence`]: { evidence: [] },
  [`/api/v1/missions/${MISSION}/connector-evidence`]: { mission_id: MISSION, connector_invocation_count: 0, connector_invocations: [] },
};

test.describe('connector impact on the mission page', () => {
  test('a partial scan with a proven mismatch shows a lower-bound exposure and never "complete"', async ({ page }) => {
    await installMocks(page, { role: 'RELEASE_OWNER', extraGet: extra });
    await page.goto(`/missions/${MISSION}`);
    await signIn(page);
    await page.getByRole('button', { name: 'Connector Impact' }).click();
    await expect(page.getByTestId('impact-status')).toHaveText('PARTIAL');
    await expect(page.getByTestId('impact-exposure')).toContainText('≥ $40,500.00');
    await expect(page.getByTestId('impact-headline')).toContainText('LOWER BOUND');
    await expect(page.getByTestId('impact-warnings')).toContainText('INCONCLUSIVE_ROWS');
    await expect(page.getByTestId('impact-status')).not.toHaveText('COMPLETE');
    await expect(page.getByTestId('impact-pipeline')).toBeVisible();
  });

  test('evidence export is offered to a release owner but hidden from a viewer (UI hint only)', async ({ page }) => {
    await installMocks(page, { role: 'RELEASE_OWNER', extraGet: extra });
    await page.goto(`/missions/${MISSION}`);
    await signIn(page);
    await page.getByRole('button', { name: 'Evidence Lineage' }).click();
    await expect(page.getByTestId('download-evidence-bundle')).toBeVisible();
  });

  test('a viewer does not see the evidence bundle button', async ({ page }) => {
    await installMocks(page, { role: 'VIEWER', extraGet: extra });
    await page.goto(`/missions/${MISSION}`);
    await signIn(page);
    await page.getByRole('button', { name: 'Evidence Lineage' }).click();
    await expect(page.getByTestId('download-evidence-bundle')).toHaveCount(0);
  });
});
