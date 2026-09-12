'use client';

import { TestScenario } from '@/lib/types/assurance';
import { ShieldAlert, CheckCircle2, AlertOctagon } from 'lucide-react';

interface ReconciliationTraceProps {
  scenario?: (TestScenario & {
    trace_differences?: Array<{
      node_id: string;
      node_type?: string;
      expected_value: string | number;
      actual_value: string | number;
      sequence_position?: number;
      is_first_divergence?: boolean;
    }>;
    first_divergent_node?: string | null;
    root_cause?: {
      node_id?: string;
      title?: string;
      explanation?: string;
      expected_value?: string | number;
      actual_value?: string | number;
    } | null;
  }) | null;
  isCompleted?: boolean;
  // Symmetric Equivalence assumes neither source is authoritative -- use
  // neutral "Source A"/"Source B" labels instead of Intent/Target framing.
  neutralLabels?: boolean;
}

export function ReconciliationTrace({ scenario, isCompleted = true, neutralLabels = false }: ReconciliationTraceProps) {
  const expectedLabel = neutralLabels ? 'Source A' : 'Intent Expected';
  const actualLabel = neutralLabels ? 'Source B' : 'Target Actual';
  if (!scenario) {
    return (
      <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-8 text-center text-slate-400">
        {isCompleted
          ? 'Select a test scenario from the Test Plan to inspect its node trace reconciliation and root cause analysis.'
          : 'Reconciliation traces will execute after test planning completes.'}
      </div>
    );
  }

  const rawTraceDiffs = scenario.trace_differences || [];
  const firstDivergence =
    scenario.first_divergent_node ||
    scenario.first_divergence_node ||
    (rawTraceDiffs.length > 0 ? rawTraceDiffs[0].node_id : null);

  const rootCause = scenario.root_cause;

  return (
    <div className="space-y-5 rounded-xl border border-slate-800 bg-slate-900/60 p-4 sm:p-6 shadow-xl">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-base font-semibold text-white flex items-center gap-2">
            <ShieldAlert className="h-5 w-5 text-sky-400" />
            Pricing Node Reconciliation Trace & RCA
          </h3>
          <p className="font-mono text-xs text-sky-400 mt-0.5">
            Scenario: {scenario.name || scenario.probe_name || scenario.experiment_id}
            {scenario.experiment_id ? ` (${scenario.experiment_id})` : ''}
          </p>
        </div>
        <span
          className={`rounded px-3 py-1 text-xs font-bold border ${
            scenario.matches
              ? 'bg-emerald-950 text-emerald-300 border-emerald-800'
              : 'bg-rose-950 text-rose-300 border-rose-800'
          }`}
        >
          {scenario.matches ? 'EXACT MATCH' : 'PRICE DIVERGENCE'}
        </span>
      </div>

      {/* Summary Box */}
      {scenario.matches ? (
        <div className="rounded-xl border border-emerald-800/80 bg-emerald-950/30 p-4 flex items-center gap-3">
          <CheckCircle2 className="h-6 w-6 text-emerald-400 shrink-0" />
          <div className="text-xs text-emerald-200">
            <span className="font-bold text-white">Full Behavioral Equivalence: </span>
            This test scenario executed end-to-end with 0 calculation trace divergences between {neutralLabels ? 'Source A and Source B' : 'canonical specification and target engine'}.
          </div>
        </div>
      ) : (
        <div className="rounded-xl border border-rose-800/80 bg-rose-950/40 p-4 space-y-2">
          <div className="flex items-center gap-2 text-rose-300 font-bold text-sm">
            <AlertOctagon className="h-5 w-5 text-rose-400 shrink-0" />
            Deterministic Root Cause Identified
          </div>
          <p className="text-xs text-rose-200 leading-relaxed">
            {rootCause?.explanation || (
              <>
                Node <span className="font-mono font-bold text-white">{firstDivergence || 'pricing_factor'}</span> produced first calculation divergence.
                {neutralLabels ? 'Source A computed' : 'Expected'} <span className="font-mono text-emerald-300 font-bold">${scenario.expected_premium}</span> but {neutralLabels ? 'Source B' : 'target implementation'} returned <span className="font-mono text-rose-300 font-bold">${scenario.actual_premium}</span>.
              </>
            )}
          </p>
        </div>
      )}

      {/* Dynamic Trace Table */}
      <div className="overflow-x-auto rounded-xl border border-slate-800 bg-slate-950">
        <table className="w-full text-left text-xs font-mono">
          <thead className="border-b border-slate-800 bg-slate-900 text-slate-400 font-sans">
            <tr>
              <th className="px-4 py-2.5">Calculation Node</th>
              <th className="px-4 py-2.5 text-emerald-400">{expectedLabel}</th>
              <th className="px-4 py-2.5 text-rose-400">{actualLabel}</th>
              <th className="px-4 py-2.5">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/60">
            {rawTraceDiffs.length > 0 ? (
              rawTraceDiffs.map((diff, idx) => {
                const isFirst = diff.node_id === firstDivergence || idx === 0;
                return (
                  <tr
                    key={diff.node_id || idx}
                    className={isFirst ? 'bg-rose-950/30 border-l-4 border-l-rose-500 font-bold' : ''}
                  >
                    <td className="px-4 py-2.5 text-slate-200">
                      {diff.node_id}
                      {isFirst && (
                        <span className="ml-2 rounded bg-rose-900 px-1.5 py-0.5 text-[10px] text-rose-200 font-sans">
                          FIRST DIVERGENCE
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-emerald-400 font-bold">{String(diff.expected_value)}</td>
                    <td className="px-4 py-2.5 text-rose-400 font-bold">{String(diff.actual_value)}</td>
                    <td className="px-4 py-2.5 text-rose-400 font-sans">Diverged</td>
                  </tr>
                );
              })
            ) : (
              <>
                <tr>
                  <td className="px-4 py-2.5 text-slate-200">base_rate_lookup</td>
                  <td className="px-4 py-2.5 text-emerald-400">Aligned</td>
                  <td className="px-4 py-2.5 text-emerald-400">Aligned</td>
                  <td className="px-4 py-2.5 text-emerald-400 font-sans">MATCH</td>
                </tr>
                <tr>
                  <td className="px-4 py-2.5 text-slate-200">final_policy_premium</td>
                  <td className="px-4 py-2.5 text-emerald-400 font-bold">${scenario.expected_premium}</td>
                  <td className="px-4 py-2.5 text-rose-400 font-bold">${scenario.actual_premium}</td>
                  <td className="px-4 py-2.5 font-sans">
                    {scenario.matches ? (
                      <span className="text-emerald-400">MATCH</span>
                    ) : (
                      <span className="text-rose-400 font-bold">MISMATCH</span>
                    )}
                  </td>
                </tr>
              </>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
