/**
 * @jest-environment jsdom
 */
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import { ConnectorImpactPanel, impactHeadline } from './ConnectorImpactPanel';
import type { ImpactAggregate, MissionImpact } from '@/lib/api/client';

const base: ImpactAggregate = {
  status: 'COMPLETE',
  impact_decision: 'PASS_ELIGIBLE',
  completeness: 'COMPLETE',
  incomplete_reasons: [],
  exposure_is_lower_bound: false,
  rows_total: 50000,
  processed_policies: 50000,
  eligible_policies: 37533,
  out_of_scope_policies: 12467,
  out_of_scope_reasons: { OUTSIDE_EFFECTIVE_PERIOD: 12467 },
  successful_comparisons: 37533,
  mismatches: 0,
  inconclusive: 0,
  unprocessed_policies: 0,
  coverage_pct: 100,
  affected_pct: 0,
  overcharge_count: 0,
  overcharge_total: '0.00',
  undercharge_count: 0,
  undercharge_total: '0.00',
  signed_net_delta: '0.00',
  absolute_exposure: '0.00',
  mean_abs_delta: null,
  median_abs_delta: null,
  min_delta: null,
  max_delta: null,
  batch_count: 250,
  batches_done: 250,
  batches_incomplete: 0,
  retry_count: 0,
  request_count: 250,
  error_classes: {},
  budget_exhausted: [],
  halt_reason: null,
  cohort_distribution: {
    minimum_cohort_size: 30,
    disclaimer: 'Screening only.',
    cohorts: [
      { cohort_dimension: 'territory', cohort_value: 'T1', sample_size: 12, suppressed: true, affected_rate: null, mean_absolute_change: null, overcharge_rate: null },
    ],
  },
  pipeline_impact: {
    as_of_date: '2026-09-20',
    buckets: [{ window_label: '0-30', affected_renewal_count: 0, total_absolute_impact: '0.00' }],
    total_affected_renewals_next_90_days: 0,
  },
  provenance: {},
  result_sha256: 'x',
};

const wrap = (agg: ImpactAggregate, extra: Partial<MissionImpact> = {}): MissionImpact => ({
  mission_id: 'MIS-1',
  status: agg.status,
  aggregate: agg,
  connector: { connector_id: 'rating-engine-demo', engine_version: 'canonical-v1', batch_quote: true },
  ...extra,
});

describe('ConnectorImpactPanel', () => {
  it('shows a complete clean scan without lower-bound wording', () => {
    render(<ConnectorImpactPanel impact={wrap(base)} />);
    expect(screen.getByTestId('impact-status')).toHaveTextContent('COMPLETE');
    expect(screen.getByTestId('impact-coverage')).toHaveTextContent('100.00%');
    expect(screen.getByTestId('impact-exposure')).not.toHaveTextContent('≥');
    expect(screen.getByTestId('impact-out-of-scope')).toHaveTextContent('12,467');
    expect(screen.getByTestId('impact-headline')).toHaveTextContent('no mismatches');
  });

  it('labels exposure as a lower bound when partial after a proven mismatch', () => {
    const agg = { ...base, status: 'PARTIAL' as const, completeness: 'PARTIAL' as const, exposure_is_lower_bound: true,
      mismatches: 10, absolute_exposure: '450.00', coverage_pct: 62.5, incomplete_reasons: ['INCONCLUSIVE_ROWS: 5 rows could not be compared.'] };
    render(<ConnectorImpactPanel impact={wrap(agg)} />);
    expect(screen.getByTestId('impact-exposure')).toHaveTextContent('≥ $450.00');
    expect(screen.getByTestId('impact-headline')).toHaveTextContent('LOWER BOUND');
    expect(screen.getByTestId('impact-warnings')).toHaveTextContent('INCONCLUSIVE_ROWS');
    expect(screen.getByTestId('impact-status')).not.toHaveTextContent('COMPLETE');
  });

  it('never calls a partial no-mismatch scan complete or zero impact', () => {
    const text = impactHeadline('PARTIAL', { ...base, exposure_is_lower_bound: false, mismatches: 0 });
    expect(text).toMatch(/zero impact cannot be inferred/i);
  });

  it('shows the reason for NOT_RUN and progress for RUNNING', () => {
    const { rerender } = render(
      <ConnectorImpactPanel impact={{ mission_id: 'M', status: 'NOT_RUN', reason: 'Connector unavailable.' }} />,
    );
    expect(screen.getByTestId('impact-headline')).toHaveTextContent('Connector unavailable.');
    rerender(
      <ConnectorImpactPanel
        impact={{
          mission_id: 'M', status: 'RUNNING',
          progress: { batches_total: 250, batches_done: 100, batches_incomplete: 0, rows_processed: 20000, rows_in_scope: 50000,
            rows_total: 50000, mismatches_so_far: 3, inconclusive_so_far: 0, retries: 1, elapsed_seconds: 12 },
        }}
      />,
    );
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '40');
  });

  it('suppresses small cohorts', () => {
    render(<ConnectorImpactPanel impact={wrap(base)} />);
    expect(screen.getByTestId('impact-cohorts')).toHaveTextContent('suppressed (n<30)');
  });
});
