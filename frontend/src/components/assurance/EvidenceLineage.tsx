'use client';

import { EvidenceRecord } from '@/lib/types/assurance';
import { useState } from 'react';
import { Database, FileText, Clock, ChevronDown, ChevronUp, Code } from 'lucide-react';

interface EvidenceLineageProps {
  evidence?: Array<EvidenceRecord & {
    title?: string;
    description?: string;
    source_ref?: string;
    target_ref?: string;
    data_summary?: Record<string, unknown>;
    created_at?: string;
  }>;
  isCompleted?: boolean;
}

export function EvidenceLineage({ evidence = [], isCompleted = true }: EvidenceLineageProps) {
  const [expandedIds, setExpandedIds] = useState<Record<string, boolean>>({});

  const toggleExpand = (id: string) => {
    setExpandedIds((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  if (!evidence || evidence.length === 0) {
    return (
      <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-8 text-center text-slate-400 text-xs">
        {isCompleted
          ? 'No audit evidence records persisted for this run.'
          : 'Evidence lineage artifacts are being generated progressively...'}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-base font-semibold text-white flex items-center gap-2">
            <Database className="h-5 w-5 text-sky-400" />
            Evidence Lineage & Audit Trail ({evidence.length} Artifacts)
          </h3>
          <p className="text-xs text-slate-400 mt-0.5">
            Immutable audit lineage connecting specification inputs, AST diffs, test executions, and final decisions
          </p>
        </div>
        <span className="text-xs font-mono text-slate-400">
          Firestore & Cloud Storage
        </span>
      </div>

      <div className="space-y-3">
        {evidence.map((ev, idx) => {
          const evId = ev.evidence_id || `EV-${idx + 1}`;
          const isExpanded = !!expandedIds[evId];
          const summaryData = ev.data_summary || ev.metadata || {};

          return (
            <div
              key={evId}
              className="rounded-xl border border-slate-800 bg-slate-900/80 p-4 transition-all hover:border-slate-700 space-y-3"
            >
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                <div className="flex items-start gap-3">
                  <div className="mt-0.5 rounded-lg bg-sky-950 p-2 text-sky-400 border border-sky-800 shrink-0">
                    <FileText className="h-4 w-4" />
                  </div>
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-bold text-white text-sm">
                        {ev.title || ev.evidence_type || 'Evidence Artifact'}
                      </span>
                      <span className="rounded bg-sky-950 px-2 py-0.5 text-[10px] font-mono font-bold text-sky-300 border border-sky-800">
                        {evId}
                      </span>
                      <span className="rounded bg-slate-800 px-2 py-0.5 text-[10px] font-mono text-slate-300">
                        {ev.evidence_type}
                      </span>
                    </div>

                    <p className="text-xs text-slate-300 mt-1 leading-relaxed">
                      {ev.description || ev.summary || 'Audit evidence record.'}
                    </p>

                    {(ev.source_ref || ev.target_ref) && (
                      <div className="mt-1.5 flex flex-wrap gap-2 text-[11px] font-mono text-slate-400">
                        {ev.source_ref && <span>Source Ref: <span className="text-emerald-400">{ev.source_ref}</span></span>}
                        {ev.target_ref && <span>Target Ref: <span className="text-rose-400">{ev.target_ref}</span></span>}
                      </div>
                    )}
                  </div>
                </div>

                <div className="flex items-center gap-3 self-end sm:self-center shrink-0">
                  <div className="flex items-center gap-1 text-[10px] font-mono text-slate-500">
                    <Clock className="h-3 w-3" />
                    <span>{ev.created_at ? new Date(ev.created_at).toLocaleTimeString() : 'Just now'}</span>
                  </div>

                  <button
                    onClick={() => toggleExpand(evId)}
                    className="inline-flex items-center gap-1 rounded bg-slate-800 px-2.5 py-1 text-[11px] font-medium text-slate-200 hover:bg-slate-700 transition-colors"
                  >
                    <Code className="h-3 w-3" />
                    {isExpanded ? 'Hide' : 'Inspect'}
                    {isExpanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                  </button>
                </div>
              </div>

              {/* Collapsible Technical JSON Viewer */}
              {isExpanded && (
                <div className="rounded-lg border border-slate-800 bg-slate-950 p-3 text-xs font-mono text-slate-300 space-y-1">
                  <div className="text-[10px] uppercase font-sans font-bold text-slate-500 mb-1">
                    Structured Evidence Data
                  </div>
                  <pre className="overflow-x-auto text-[11px] text-sky-300 leading-tight">
                    {JSON.stringify(summaryData, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
