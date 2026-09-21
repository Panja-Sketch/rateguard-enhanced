'use client';

import type { ImpactAggregate, ImpactStatus, MissionImpact } from '@/lib/api/client';

const STATUS_STYLE: Record<ImpactStatus, string> = {
  NOT_RUN: 'bg-slate-800 text-slate-300 border-slate-700',
  QUEUED: 'bg-slate-800 text-slate-200 border-slate-700',
  RUNNING: 'bg-sky-950 text-sky-300 border-sky-800',
  COMPLETE: 'bg-emerald-950 text-emerald-300 border-emerald-800',
  PARTIAL: 'bg-amber-950 text-amber-300 border-amber-800',
  CANCELLED: 'bg-slate-800 text-slate-300 border-slate-700',
  FAILED: 'bg-rose-950 text-rose-300 border-rose-800',
};

function usd(v: string | null | undefined): string {
  if (v === null || v === undefined) return '—';
  const n = Number(v);
  if (!Number.isFinite(n)) return v;
  return `${n < 0 ? '-' : ''}$${Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function pct(v: number | null | undefined): string {
  return v === null || v === undefined ? '—' : `${v.toFixed(2)}%`;
}

function Stat({ label, value, testId, tone }: { label: string; value: string; testId?: string; tone?: string }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-3" data-testid={testId}>
      <div className="text-[10px] uppercase tracking-wide text-slate-400">{label}</div>
      <div className={`mt-1 font-mono text-base font-bold ${tone ?? 'text-white'}`}>{value}</div>
    </div>
  );
}

/** Human explanation for a non-complete impact — never presented as complete or exact. */
export function impactHeadline(status: ImpactStatus, agg: ImpactAggregate | null | undefined, reason?: string): string {
  if (status === 'NOT_RUN') return reason || 'Connector-backed portfolio impact was not run for this mission.';
  if (status === 'QUEUED') return reason || 'Waiting for the mission to reach the portfolio impact stage.';
  if (status === 'RUNNING') return 'Repricing the portfolio through the authoritative source and the connector…';
  if (status === 'CANCELLED') return 'The scan was cancelled. Partial results are shown and are never treated as a pass.';
  if (status === 'FAILED') return 'The scan failed internally; no impact conclusion can be drawn.';
  if (status === 'PARTIAL') {
    return agg?.exposure_is_lower_bound
      ? 'PARTIAL scan with proven mismatches: exposure below is a LOWER BOUND, not the full exposure. Deployment stays blocked.'
      : 'PARTIAL scan with no proven mismatch: zero impact cannot be inferred from incomplete data. Review is required.';
  }
  return agg && agg.mismatches > 0
    ? 'Complete scan: mismatches were proven for the eligible portfolio.'
    : 'Complete scan: no mismatches were found across the eligible portfolio.';
}

export function ConnectorImpactPanel({ impact }: { impact: MissionImpact | null }) {
  if (!impact) {
    return (
      <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-8 text-center text-xs text-slate-400 font-mono">
        Connector impact status is not available yet.
      </div>
    );
  }
  const agg = impact.aggregate ?? null;
  const status: ImpactStatus = (agg?.status as ImpactStatus) ?? impact.status;
  const p = impact.progress;
  const lowerBound = !!agg?.exposure_is_lower_bound;
  const batchesDone = agg?.batches_done ?? p?.batches_done ?? 0;
  const batchesTotal = agg?.batch_count ?? p?.batches_total ?? 0;
  const running = status === 'RUNNING' || status === 'QUEUED';

  return (
    <div className="space-y-5" data-testid="connector-impact-panel">
      <div className="flex flex-wrap items-center gap-3">
        <span
          className={`rounded border px-2.5 py-0.5 font-mono text-xs font-bold ${STATUS_STYLE[status]}`}
          data-testid="impact-status"
        >
          {status}
        </span>
        {impact.connector && (
          <span className="font-mono text-[11px] text-slate-400">
            {impact.connector.connector_id}@{impact.connector.engine_version}
            {impact.connector.batch_quote ? ' · batch quote' : ' · single quote'}
          </span>
        )}
      </div>

      <p
        className={`rounded-lg border p-3 text-xs leading-relaxed ${
          status === 'PARTIAL' || status === 'FAILED'
            ? 'border-amber-800/60 bg-amber-950/20 text-amber-200'
            : 'border-slate-800 bg-slate-900/50 text-slate-300'
        }`}
        data-testid="impact-headline"
      >
        {impactHeadline(status, agg, impact.reason)}
      </p>

      {(p || agg) && (
        <div className="space-y-1.5">
          <div className="flex justify-between font-mono text-[11px] text-slate-400">
            <span>
              Batches {batchesDone}/{batchesTotal}
            </span>
            <span>
              {agg
                ? `${agg.successful_comparisons.toLocaleString()} of ${agg.eligible_policies.toLocaleString()} eligible compared`
                : `${(p?.rows_processed ?? 0).toLocaleString()} of ${(p?.rows_in_scope ?? 0).toLocaleString()} processed`}
            </span>
          </div>
          <div className="h-2 overflow-hidden rounded bg-slate-800" role="progressbar"
            aria-valuenow={batchesTotal ? Math.round((batchesDone / batchesTotal) * 100) : 0} aria-valuemin={0} aria-valuemax={100}>
            <div
              className={`h-full ${running ? 'bg-sky-500 animate-pulse' : status === 'COMPLETE' ? 'bg-emerald-500' : 'bg-amber-500'}`}
              style={{ width: `${batchesTotal ? Math.round((batchesDone / batchesTotal) * 100) : 0}%` }}
            />
          </div>
          {p && running && (
            <div className="font-mono text-[11px] text-slate-400">
              {p.mismatches_so_far} mismatches so far · {p.inconclusive_so_far} inconclusive · {p.retries} retries ·{' '}
              {p.elapsed_seconds}s
            </div>
          )}
        </div>
      )}

      {agg && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Coverage" value={pct(agg.coverage_pct)} testId="impact-coverage"
              tone={agg.completeness === 'COMPLETE' ? 'text-emerald-300' : 'text-amber-300'} />
            <Stat label="Mismatches" value={agg.mismatches.toLocaleString()} testId="impact-mismatches"
              tone={agg.mismatches ? 'text-rose-300' : 'text-white'} />
            <Stat label="Inconclusive" value={agg.inconclusive.toLocaleString()} testId="impact-inconclusive"
              tone={agg.inconclusive ? 'text-amber-300' : 'text-white'} />
            <Stat label="Affected" value={pct(agg.affected_pct)} />
            <Stat label={lowerBound ? 'Absolute exposure (lower bound)' : 'Absolute exposure'}
              value={`${lowerBound ? '≥ ' : ''}${usd(agg.absolute_exposure)}`} testId="impact-exposure" />
            <Stat label="Net delta (signed)" value={usd(agg.signed_net_delta)} />
            <Stat label="Undercharged" value={`${agg.undercharge_count.toLocaleString()} · ${usd(agg.undercharge_total)}`} />
            <Stat label="Overcharged" value={`${agg.overcharge_count.toLocaleString()} · ${usd(agg.overcharge_total)}`} />
            <Stat label="Mean |Δ| / Median |Δ|" value={`${usd(agg.mean_abs_delta)} / ${usd(agg.median_abs_delta)}`} />
            <Stat label="Min / Max Δ" value={`${usd(agg.min_delta)} / ${usd(agg.max_delta)}`} />
            <Stat label="Retries / Requests" value={`${agg.retry_count} / ${agg.request_count}`} />
            <Stat label="Out of scope" value={agg.out_of_scope_policies.toLocaleString()} />
          </div>

          {agg.out_of_scope_policies > 0 && (
            <p className="text-[11px] text-slate-400" data-testid="impact-out-of-scope">
              {agg.out_of_scope_policies.toLocaleString()} policies were not repriced because the authoritative source does
              not apply to them (
              {Object.entries(agg.out_of_scope_reasons)
                .map(([k, v]) => `${k.replace(/_/g, ' ').toLowerCase()}: ${v.toLocaleString()}`)
                .join(', ')}
              ).
            </p>
          )}

          {(agg.incomplete_reasons.length > 0 || agg.budget_exhausted.length > 0 || agg.halt_reason) && (
            <ul className="space-y-1 rounded-lg border border-amber-800/60 bg-amber-950/20 p-3 text-[11px] text-amber-200"
              data-testid="impact-warnings">
              {agg.incomplete_reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
              {agg.halt_reason && <li>Scan halted: {agg.halt_reason}</li>}
              {Object.entries(agg.error_classes).map(([code, n]) => (
                <li key={code}>
                  {code}: {n.toLocaleString()} rows
                </li>
              ))}
            </ul>
          )}

          {agg.pipeline_impact && (
            <div>
              <h4 className="mb-2 text-xs font-bold text-slate-200">Renewal impact — next 30 / 60 / 90 days</h4>
              <div className="grid grid-cols-3 gap-3" data-testid="impact-pipeline">
                {agg.pipeline_impact.buckets.map((b) => (
                  <Stat key={b.window_label} label={`${b.window_label} days`}
                    value={`${b.affected_renewal_count.toLocaleString()} · ${usd(b.total_absolute_impact)}`} />
                ))}
              </div>
            </div>
          )}

          {agg.cohort_distribution && agg.cohort_distribution.cohorts.length > 0 && (
            <div>
              <h4 className="mb-2 text-xs font-bold text-slate-200">Cohort distribution</h4>
              <div className="overflow-x-auto rounded-lg border border-slate-800">
                <table className="w-full text-left text-[11px]" data-testid="impact-cohorts">
                  <thead className="bg-slate-900 text-slate-400">
                    <tr>
                      <th className="p-2">Dimension</th>
                      <th className="p-2">Cohort</th>
                      <th className="p-2">Size</th>
                      <th className="p-2">Affected</th>
                      <th className="p-2">Mean change</th>
                    </tr>
                  </thead>
                  <tbody className="font-mono text-slate-200">
                    {agg.cohort_distribution.cohorts.map((c) => (
                      <tr key={`${c.cohort_dimension}:${c.cohort_value}`} className="border-t border-slate-800">
                        <td className="p-2">{c.cohort_dimension}</td>
                        <td className="p-2">{c.cohort_value}</td>
                        <td className="p-2">{c.sample_size.toLocaleString()}</td>
                        <td className="p-2">
                          {c.suppressed ? 'suppressed (n<' + agg.cohort_distribution!.minimum_cohort_size + ')' : pct((c.affected_rate ?? 0) * 100)}
                        </td>
                        <td className="p-2">{c.suppressed ? '—' : usd(c.mean_absolute_change)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="mt-2 text-[10px] text-slate-500">{agg.cohort_distribution.disclaimer}</p>
            </div>
          )}
        </>
      )}
    </div>
  );
}
