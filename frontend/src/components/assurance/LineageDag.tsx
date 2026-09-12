'use client';

import { FileSpreadsheet, Server, FileCode, CheckCircle2, ArrowRight } from 'lucide-react';

export function LineageDag() {
  const steps = [
    {
      title: 'Pricing Intent',
      subtitle: 'Actuarial Spec / Excel',
      icon: FileSpreadsheet,
      color: 'border-sky-500/40 bg-sky-950/40 text-sky-300',
    },
    {
      title: 'Canonical IPIR',
      subtitle: 'Authoritative Model A',
      icon: FileCode,
      color: 'border-blue-500/40 bg-blue-950/40 text-blue-300',
    },
    {
      title: 'Semantic Diff',
      subtitle: 'Bidirectional Comparison',
      icon: CheckCircle2,
      color: 'border-indigo-500/40 bg-indigo-950/40 text-indigo-300',
    },
    {
      title: 'Target Impl',
      subtitle: 'Platform Config B',
      icon: Server,
      color: 'border-purple-500/40 bg-purple-950/40 text-purple-300',
    },
  ];

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4 sm:p-6 shadow-xl">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-white">Source-Agnostic Assurance Lineage</h3>
          <p className="text-xs text-slate-400">Bidirectional compilation into IPIR for deterministic assurance</p>
        </div>
        <span className="rounded bg-sky-950 px-2 py-0.5 text-xs font-mono font-medium text-sky-400 border border-sky-800">
          IPIR 0.1
        </span>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-4">
        {steps.map((step, idx) => {
          const Icon = step.icon;
          return (
            <div key={idx} className="relative flex flex-col items-center">
              <div className={`flex w-full flex-col items-center rounded-lg border p-3 text-center transition-all ${step.color}`}>
                <Icon className="mb-1.5 h-5 w-5" />
                <span className="text-xs font-bold">{step.title}</span>
                <span className="text-[10px] text-slate-400">{step.subtitle}</span>
              </div>
              {idx < steps.length - 1 && (
                <div className="hidden sm:absolute sm:-right-3 sm:top-1/2 sm:-translate-y-1/2 sm:translate-x-1/2 sm:block z-10">
                  <ArrowRight className="h-4 w-4 text-slate-600" />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

