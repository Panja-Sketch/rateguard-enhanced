'use client';

import { useEffect, useState } from 'react';
import { Network, Database, Bot, Cpu, Lock, Server, ShieldCheck, ArrowDown } from 'lucide-react';
import { fetchSystemInfo } from '@/lib/api/client';

const DEFAULT_MODEL_LABEL = 'Gemini 3.7 Flash';
const DEFAULT_PROVIDER_LABEL = 'Google Vertex AI';
const DEFAULT_FRAMEWORK_LABEL = 'Google GenAI SDK';
const DEFAULT_SUPERVISOR_LABEL = 'Google GenAI SDK Structured-Decision Supervisor';

export default function ArchitecturePage() {
  // Backend runtime metadata (GET /api/v1/system/info), used where practical
  // so the displayed model/provider/framework can never drift from what the
  // deployed backend actually reports. Falls back to the same truthful
  // static labels on a fetch failure -- this page must never regress to
  // showing nothing, and never fabricates ADK/multi-agent claims either way.
  const [modelLabel, setModelLabel] = useState(DEFAULT_MODEL_LABEL);
  const [providerLabel, setProviderLabel] = useState(DEFAULT_PROVIDER_LABEL);
  const [frameworkLabel, setFrameworkLabel] = useState(DEFAULT_FRAMEWORK_LABEL);
  const [supervisorLabel, setSupervisorLabel] = useState(DEFAULT_SUPERVISOR_LABEL);

  useEffect(() => {
    fetchSystemInfo()
      .then((info) => {
        if (info.gemini_model_display) setModelLabel(info.gemini_model_display);
        if (info.agent_provider) setProviderLabel(info.agent_provider);
        if (info.agent_framework) setFrameworkLabel(info.agent_framework);
        if (info.agent_supervisor) setSupervisorLabel(info.agent_supervisor);
      })
      .catch(() => {
        // Backend unreachable: keep the static, equally truthful defaults above.
      });
  }, []);

  const techStack = [
    {
      title: `${frameworkLabel} + ${modelLabel}`,
      category: 'Agentic Supervisor',
      desc: 'Orchestrates adaptive assurance missions, interprets findings, plans probes, synthesizes root causes, and proposes remediation patches.',
      icon: Bot,
      color: 'border-sky-500/50 bg-sky-950/30 text-sky-300',
    },
    {
      title: 'BigQuery (50K Portfolio)',
      category: 'Portfolio Analytics',
      desc: 'Stores 50,000 synthetic carrier policies and executes high-performance SQL exposure queries measuring financial blast radius.',
      icon: Database,
      color: 'border-blue-500/50 bg-blue-950/30 text-blue-300',
    },
    {
      title: 'Firestore (Native)',
      category: 'Workflow State & Audit',
      desc: 'Persists real-time assurance mission state, event logs, and audit evidence records in Native mode database.',
      icon: Server,
      color: 'border-emerald-500/50 bg-emerald-950/30 text-emerald-300',
    },
    {
      title: 'Cloud Storage (GCS)',
      category: 'Artifact Management',
      desc: 'Stores pricing source files, compiled IPIR AST models, semantic diff reports, and execution traces in gs://rateguard-ai-artifacts.',
      icon: Database,
      color: 'border-amber-500/50 bg-amber-950/30 text-amber-300',
    },
    {
      title: 'Pub/Sub Worker Queue',
      category: 'Async Queue',
      desc: 'Delivers asynchronous assurance jobs via authenticated push subscription to Cloud Run worker endpoints for background execution.',
      icon: Cpu,
      color: 'border-rose-500/50 bg-rose-950/30 text-rose-300',
    },
  ];

  return (
    <div className="space-y-8 max-w-5xl mx-auto">
      <div>
        <h1 className="text-2xl sm:text-3xl font-extrabold text-white flex items-center gap-2">
          <Network className="h-7 w-7 text-sky-400" /> RateGuard System Architecture V2
        </h1>
        <p className="text-xs sm:text-sm text-slate-400 mt-1">
          Combining deterministic software calculation with agentic AI orchestration on Google Cloud.
        </p>
      </div>

      {/* Architecture Topology Box */}
      <div className="rounded-2xl border border-slate-800 bg-slate-900/80 p-6 space-y-4 shadow-xl">
        <h2 className="text-base font-bold text-white uppercase tracking-wider">
          End-to-End System Topology & Integration Flow
        </h2>

        <div className="rounded-xl border border-slate-800 bg-slate-950 p-4 font-mono text-xs text-sky-300 overflow-x-auto space-y-2 leading-relaxed">
          <div>User Browser / Client → Next.js 14 Frontend</div>
          <div className="pl-4 text-slate-500 font-sans">↓ HTTP REST API</div>
          <div>FastAPI Backend (Cloud Run API)</div>
          <div className="pl-4 text-slate-500 font-sans">↓ Pub/Sub Async Queue / Native Invocation</div>
          <div>Private Worker Runtime (Cloud Run Worker)</div>
          <div className="pl-4 text-slate-500 font-sans">↓ Agentic Assurance Supervisor</div>
          <div className="text-purple-300 font-bold">{supervisorLabel} ↔ {modelLabel} ({providerLabel})</div>
          <div className="pl-4 text-slate-500 font-sans">↕ Deterministic Boundaries</div>
          <div>Python Deterministic Engines (AST Diff, Oracle Math, Test Generator, Reconciliation)</div>
          <div className="pl-4 text-slate-500 font-sans">↓ Cloud Persistence</div>
          <div>Firestore (RunState) | BigQuery (50K Portfolio SQL) | GCS (Artifacts)</div>
          <div className="pl-4 text-slate-500 font-sans">↓ Release Decision</div>
          <div className="text-white font-bold font-sans">PASS / REVIEW_REQUIRED / BLOCK_DEPLOYMENT</div>
        </div>
      </div>

      {/* Core Principle Alert */}
      <div className="rounded-xl border border-sky-800 bg-sky-950/40 p-4 space-y-2">
        <div className="flex items-center gap-2 font-bold text-sky-300 text-sm">
          <Lock className="h-4 w-4" /> Strict Deterministic Boundary Guarantee
        </div>
        <p className="text-xs text-sky-100 leading-relaxed">
          {modelLabel}, invoked through the {frameworkLabel}, plans investigation probes, reasons over AST diffs, proposes remediation patches, and synthesizes executive summaries. All monetary arithmetic, rate table lookups, pricing graph evaluations, test executions, BigQuery SQL aggregations, and financial exposure calculations are executed exclusively by deterministic Python code using Python <code className="font-mono text-white font-bold">Decimal</code>.
        </p>
      </div>

      {/* Tech Stack Cards */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {techStack.map((tech, idx) => {
          const Icon = tech.icon;
          return (
            <div key={idx} className={`rounded-xl border p-4 shadow-lg space-y-2 ${tech.color}`}>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 font-bold text-white text-sm">
                  <Icon className="h-5 w-5" />
                  {tech.title}
                </div>
                <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-slate-900/80 border border-slate-700">
                  {tech.category}
                </span>
              </div>
              <p className="text-xs text-slate-300 leading-relaxed">{tech.desc}</p>
            </div>
          );
        })}
      </div>
    </div>
  );
}
