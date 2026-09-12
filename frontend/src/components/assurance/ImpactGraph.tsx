'use client';

import { ImpactAnalysis } from '@/lib/types/assurance';
import { Network, AlertCircle, ArrowDown, CheckCircle2 } from 'lucide-react';

interface ImpactGraphProps {
  impact?: ImpactAnalysis | {
    changed_nodes?: string[];
    affected_nodes?: string[];
    affected_outputs?: string[];
    impacted_calculation_nodes?: string[];
    affected_pricing_outputs?: string[];
    candidate_risk_predicates?: Array<{
      id: string;
      description: string;
    }>;
  } | null;
  isCompleted?: boolean;
}

export function ImpactGraph({ impact, isCompleted = true }: ImpactGraphProps) {
  const changedNodes =
    (impact as any)?.changed_nodes ||
    (impact as any)?.impacted_calculation_nodes?.filter((n: string) => !n.includes('premium')) ||
    [];
  const calcNodes =
    (impact as any)?.impacted_calculation_nodes ||
    (impact as any)?.affected_nodes ||
    [];
  const outputNodes =
    (impact as any)?.affected_pricing_outputs ||
    (impact as any)?.affected_outputs ||
    ['final_policy_premium'];
  const predicates = (impact as any)?.candidate_risk_predicates || [];

  const hasImpact = changedNodes.length > 0 || calcNodes.length > 0;

  if (!hasImpact && isCompleted) {
    return (
      <div className="rounded-xl border border-emerald-800/60 bg-emerald-950/20 p-8 text-center space-y-3">
        <CheckCircle2 className="h-10 w-10 text-emerald-400 mx-auto" />
        <h4 className="text-sm font-bold text-emerald-300">No Impacted Pricing Paths</h4>
        <p className="text-xs text-slate-300 max-w-md mx-auto">
          Directed Acyclic Graph (DAG) traversal completed with 0 affected downstream calculation nodes or outputs.
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4 sm:p-6 shadow-xl space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-base font-semibold text-white flex items-center gap-2">
            <Network className="h-5 w-5 text-sky-400" />
            Pricing Dependency Graph & Blast Radius DAG
          </h3>
          <p className="text-xs text-slate-400 mt-0.5">
            Dynamic AST graph traversal mapping semantic changes to downstream premium calculations
          </p>
        </div>
        <span className="text-xs font-mono text-sky-300 rounded bg-sky-950 px-2 py-0.5 border border-sky-800">
          AST DAG Traversal
        </span>
      </div>

      {/* Dynamic Graph Flow */}
      <div className="flex flex-col items-center gap-2 py-2">
        {/* Changed Semantic Nodes */}
        <div className="flex flex-col items-center w-full max-w-lg">
          <div className="w-full rounded-xl border border-rose-800 bg-rose-950/40 p-4 text-center shadow-lg shadow-rose-950/30">
            <div className="flex items-center justify-center gap-2 text-rose-300 font-bold text-xs sm:text-sm">
              <AlertCircle className="h-4 w-4 text-rose-400" />
              <span>Changed Semantic Nodes ({changedNodes.length || 1})</span>
            </div>
            <div className="mt-2 flex flex-wrap justify-center gap-1.5 font-mono text-xs text-rose-200">
              {changedNodes.length > 0 ? (
                changedNodes.map((n: string) => (
                  <span key={n} className="rounded bg-rose-900/80 px-2 py-0.5 border border-rose-700">
                    {n}
                  </span>
                ))
              ) : (
                <span className="rounded bg-rose-900/80 px-2 py-0.5 border border-rose-700">
                  direct_factor_override
                </span>
              )}
            </div>
          </div>
          <ArrowDown className="my-1.5 h-4 w-4 text-slate-600" />
        </div>

        {/* Downstream Calculation Chain */}
        <div className="flex flex-col items-center w-full max-w-lg">
          <div className="w-full rounded-xl border border-amber-800/80 bg-amber-950/30 p-4 text-center shadow-lg">
            <div className="text-amber-300 font-bold text-xs sm:text-sm">
              Downstream Calculation Nodes ({calcNodes.length || 1})
            </div>
            <div className="mt-2 flex flex-wrap justify-center gap-1.5 font-mono text-xs text-amber-200">
              {calcNodes.length > 0 ? (
                calcNodes.map((n: string) => (
                  <span key={n} className="rounded bg-amber-900/60 px-2 py-0.5 border border-amber-700">
                    {n}
                  </span>
                ))
              ) : (
                <span className="rounded bg-amber-900/60 px-2 py-0.5 border border-amber-700">
                  gross_risk_premium
                </span>
              )}
            </div>
          </div>
          <ArrowDown className="my-1.5 h-4 w-4 text-slate-600" />
        </div>

        {/* Impacted Final Outputs */}
        <div className="flex flex-col items-center w-full max-w-lg">
          <div className="w-full rounded-xl border border-emerald-800/80 bg-emerald-950/30 p-4 text-center shadow-lg">
            <div className="text-emerald-300 font-bold text-xs sm:text-sm">
              Impacted Pricing Outputs ({outputNodes.length || 1})
            </div>
            <div className="mt-2 flex flex-wrap justify-center gap-1.5 font-mono text-xs text-emerald-200">
              {outputNodes.map((n: string) => (
                <span key={n} className="rounded bg-emerald-900/60 px-2 py-0.5 border border-emerald-700">
                  {n}
                </span>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Predicates */}
      {predicates.length > 0 && (
        <div className="rounded-xl border border-slate-800 bg-slate-950 p-4 space-y-2">
          <h4 className="text-xs font-bold text-slate-300 uppercase tracking-wider">
            Identified Risk Predicates ({predicates.length})
          </h4>
          <div className="space-y-1.5">
            {predicates.map((p: any, idx: number) => (
              <div key={p.id || idx} className="rounded bg-slate-900 p-2 text-xs font-mono text-sky-300 flex items-center gap-2">
                <span className="rounded bg-sky-950 px-1.5 py-0.5 text-[10px] text-sky-400 border border-sky-800">
                  {p.id || `P-${idx + 1}`}
                </span>
                <span className="text-slate-200 font-sans">{p.description || JSON.stringify(p)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
