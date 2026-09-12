import Link from 'next/link';
import { LineageDag } from '@/components/assurance/LineageDag';
import {
  ShieldCheck,
  Cpu,
  FileCode2,
  TrendingDown,
  Lock,
  GitCompare,
  ArrowRight,
  Database,
} from 'lucide-react';

export default function HomePage() {
  return (
    <div className="space-y-10">
      {/* Hero Section */}
      <section className="relative rounded-2xl border border-slate-800 bg-slate-900/60 p-6 sm:p-10 shadow-2xl overflow-hidden">
        <div className="absolute -top-24 -right-24 h-96 w-96 rounded-full bg-sky-600/10 blur-3xl" />
        <div className="relative z-10 max-w-3xl space-y-4">
          <div className="inline-flex items-center gap-2 rounded-full bg-sky-950 px-3 py-1 text-xs font-semibold text-sky-300 border border-sky-800">
            <ShieldCheck className="h-4 w-4" /> Autonomous Pricing Assurance Engine
          </div>
          <h1 className="text-3xl font-extrabold text-white sm:text-5xl tracking-tight leading-tight">
            Continuous Pricing Assurance for <span className="text-sky-400">Insurance</span>
          </h1>
          <p className="text-sm sm:text-base text-slate-300 leading-relaxed">
            Insurance pricing moves across regulatory filings, actuarial workbooks, pricing systems, policy administration APIs, and production environments. RateGuard AI independently compiles pricing representations into canonical IPIR ASTs, detects semantic drift, executes risk-directed test suites, and evaluates financial exposure across 50,000 policy portfolios.
          </p>
          <div className="pt-2 flex flex-wrap items-center gap-4">
            <Link
              href="/missions/new"
              className="inline-flex items-center gap-2 rounded-lg bg-sky-600 px-5 py-2.5 text-sm font-bold text-white shadow-lg shadow-sky-600/30 hover:bg-sky-500 transition-all"
            >
              <Cpu className="h-4 w-4" /> Start Assurance Mission <ArrowRight className="h-4 w-4" />
            </Link>
            <Link
              href="/architecture"
              className="inline-flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-800 px-5 py-2.5 text-sm font-semibold text-slate-200 hover:bg-slate-750 transition-all"
            >
              View System Architecture
            </Link>
          </div>
        </div>
      </section>

      {/* Feature Highlights Grid */}
      <section className="grid grid-cols-1 gap-6 sm:grid-cols-3">
        <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-5 space-y-2">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-sky-950 text-sky-400 border border-sky-800">
            <FileCode2 className="h-5 w-5" />
          </div>
          <h3 className="text-base font-bold text-white">Vendor-Neutral Intermediate Representation</h3>
          <p className="text-xs text-slate-400 leading-relaxed">
            Compiles supported native IPIR and structured rating-config JSON sources into a canonical Intermediate Pricing Implementation Representation (IPIR) for symmetric comparison. Excel and PDF extraction is planned, not yet supported.
          </p>
        </div>

        <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-5 space-y-2">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-purple-950 text-purple-400 border border-purple-800">
            <Lock className="h-5 w-5" />
          </div>
          <h3 className="text-base font-bold text-white">Authoritative Deterministic Math</h3>
          <p className="text-xs text-slate-400 leading-relaxed">
            Gemini orchestrates and explains findings, but all factor lookup, calculation node ordering, and premium math are executed by deterministic Python code using Python <code className="text-sky-300 font-mono">Decimal</code>.
          </p>
        </div>

        <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-5 space-y-2">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-emerald-950 text-emerald-400 border border-emerald-800">
            <Database className="h-5 w-5" />
          </div>
          <h3 className="text-base font-bold text-white">50K BigQuery Portfolio Blast Radius</h3>
          <p className="text-xs text-slate-400 leading-relaxed">
            Evaluates financial impact and premium leakage across a BigQuery-backed 50,000-policy synthetic portfolio to output authoritative <code className="text-rose-300 font-mono font-bold">BLOCK DEPLOYMENT</code> decisions.
          </p>
        </div>
      </section>

      {/* Assurance Workflow Pipeline */}
      <section className="space-y-4">
        <h2 className="text-xl font-bold text-white flex items-center gap-2">
          <GitCompare className="h-5 w-5 text-sky-400" /> End-to-End Assurance Workflow
        </h2>
        <LineageDag />
      </section>
    </div>
  );
}

