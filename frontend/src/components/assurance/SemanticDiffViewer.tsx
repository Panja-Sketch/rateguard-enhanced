'use client';

import { SemanticDiffItem } from '@/lib/types/assurance';
import { AlertTriangle, CheckCircle2, ShieldAlert } from 'lucide-react';

interface SemanticDiffViewerProps {
  diffs?: SemanticDiffItem[];
  isCompleted?: boolean;
  // Symmetric Equivalence assumes neither source is authoritative -- use
  // neutral "Source A"/"Source B" labels instead of Release Conformance's
  // Intent/Target framing.
  neutralLabels?: boolean;
}

export function SemanticDiffViewer({ diffs, isCompleted = true, neutralLabels = false }: SemanticDiffViewerProps) {
  const intentLabel = neutralLabels ? 'Source A:' : 'Intent:';
  const targetLabel = neutralLabels ? 'Source B:' : 'Target:';
  if (!diffs || diffs.length === 0) {
    return (
      <div className="rounded-xl border border-emerald-800/60 bg-emerald-950/20 p-8 text-center space-y-3">
        <CheckCircle2 className="h-10 w-10 text-emerald-400 mx-auto" />
        <h4 className="text-sm font-bold text-emerald-300">
          {isCompleted
            ? 'No Material Pricing Drift Detected'
            : 'Waiting for Semantic Analysis...'}
        </h4>
        <p className="text-xs text-slate-300 max-w-md mx-auto">
          {isCompleted
            ? neutralLabels
              ? 'Deterministic IPIR 0.1 AST comparison verified full equivalence between Source A and Source B.'
              : 'Deterministic IPIR 0.1 AST comparison verified full equivalence between pricing intent and target implementation.'
            : 'Semantic assurance agent will compare pricing representation ASTs once compiled.'}
        </p>
      </div>
    );
  }

  const getSeverityBadge = (severity: string) => {
    switch (severity?.toUpperCase()) {
      case 'CRITICAL':
        return 'bg-rose-950 text-rose-300 border-rose-800';
      case 'HIGH':
        return 'bg-amber-950 text-amber-300 border-amber-800';
      case 'MEDIUM':
        return 'bg-yellow-950 text-yellow-300 border-yellow-800';
      default:
        return 'bg-slate-800 text-slate-300 border-slate-700';
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-base font-semibold text-white flex items-center gap-2">
          <ShieldAlert className="h-5 w-5 text-rose-400" />
          Semantic Differences ({diffs.length})
        </h3>
        <span className="text-xs text-slate-400">Identified via IPIR 0.1 AST Comparison</span>
      </div>

      <div className="space-y-3">
        {diffs.map((diff, idx) => {
          // The backend's MaterialFinding sends intent_value/target_value (and
          // folds difference_type/semantic_path into `category`/`title`) --
          // left_value/right_value/difference_type/semantic_path only ever
          // come from a raw SemanticDifference, never from a real mission
          // payload. Prefer whichever the response actually populated, and
          // hide a label entirely rather than render it next to nothing.
          const intentValue = diff.intent_value ?? diff.left_value;
          const targetValue = diff.target_value ?? diff.right_value;
          const typeLabel = diff.difference_type || diff.category;
          const pathLabel = diff.semantic_path;

          return (
          <div
            key={diff.id || idx}
            className="rounded-lg border border-slate-800 bg-slate-900/80 p-4 transition-all hover:border-slate-700"
          >
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <span
                  className={`rounded px-2 py-0.5 text-xs font-semibold border ${getSeverityBadge(
                    diff.severity
                  )}`}
                >
                  {diff.severity}
                </span>
                {typeLabel && (
                  <span className="font-mono text-xs font-bold text-sky-400">
                    {typeLabel}
                  </span>
                )}
              </div>
              {pathLabel && (
                <span className="font-mono text-xs text-slate-400">
                  {pathLabel}
                </span>
              )}
            </div>

            <p className="mb-3 text-sm text-slate-200">{diff.description}</p>

            {(intentValue || targetValue) && (
              <div className="grid grid-cols-1 gap-2 rounded border border-slate-800 bg-slate-950 p-2.5 sm:grid-cols-2 text-xs font-mono">
                {intentValue && (
                  <div className="flex items-center gap-2 border-b border-slate-800 pb-1.5 sm:border-b-0 sm:border-r sm:pr-2 sm:pb-0">
                    <span className="text-slate-500 font-sans">{intentLabel}</span>
                    <span className="text-emerald-400 font-bold">{intentValue}</span>
                  </div>
                )}
                {targetValue && (
                  <div className="flex items-center gap-2">
                    <span className="text-slate-500 font-sans">{targetLabel}</span>
                    <span className="text-rose-400 font-bold">{targetValue}</span>
                  </div>
                )}
              </div>
            )}

            {diff.affected_output && (
              <div className="mt-2 flex items-center gap-1.5 text-[11px] text-slate-400">
                <AlertTriangle className="h-3.5 w-3.5 text-amber-400" />
                <span>Impacted Output Node:</span>
                <span className="font-mono text-slate-300">{diff.affected_output}</span>
              </div>
            )}
          </div>
          );
        })}
      </div>
    </div>
  );
}
