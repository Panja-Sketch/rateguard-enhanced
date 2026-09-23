import Link from 'next/link';
import { LineageDag } from '@/components/assurance/LineageDag';
import {
  ShieldCheck,
  Cpu,
  FileCode2,
  Lock,
  GitCompare,
  ArrowRight,
  Database,
  ClipboardCheck,
  ScaleIcon,
} from 'lucide-react';

const NARRATIVE_STEPS = [
  {
    n: '1',
    title: 'Approved pricing intent enters',
    desc: 'A controlled workbook (RateGuard Controlled Workbook v1) or strict IPIR JSON — the approved actuarial specification, not the deployed code.',
  },
  {
    n: '2',
    title: 'Candidate implementation is reached',
    desc: 'Via a versioned, authenticated REST rating-engine connector, or a second IPIR JSON package — never an arbitrary URL, never source code.',
  },
  {
    n: '3',
    title: 'Targeted probes are generated',
    desc: 'A control case plus boundary tests around every changed rule, so verification is proportionate instead of brute force.',
  },
  {
    n: '4',
    title: 'Approved intent is compared to the deployed engine',
    desc: 'Exact Decimal arithmetic, node-by-node, with the first point of divergence identified.',
  },
  {
    n: '5',
    title: 'Bounded portfolio impact is quantified',
    desc: 'A confirmed defect is run against a synthetic, seeded 50,000-policy portfolio to count affected policies and dollar exposure — never live production data.',
  },
  {
    n: '6',
    title: 'A release decision is returned',
    desc: 'PASS, BLOCK_DEPLOYMENT, or REVIEW_REQUIRED — the same conservative gate regardless of what kind of target the connector points at.',
  },
  {
    n: '7',
    title: 'Customer impact and evidence are produced',
    desc: 'Affected-policy counts, overcharge/undercharge dollars, renewal timing, and a SHA-256 hashed evidence bundle with a manifest for release, compliance, and customer-protection review.',
  },
];

export default function HomePage() {
  return (
    <div className="space-y-10">
      {/* Hero Section */}
      <section className="relative rounded-2xl border border-slate-800 bg-slate-900/60 p-6 sm:p-10 shadow-2xl overflow-hidden">
        <div className="absolute -top-24 -right-24 h-96 w-96 rounded-full bg-sky-600/10 blur-3xl" />
        <div className="relative z-10 max-w-3xl space-y-4">
          <div className="inline-flex items-center gap-2 rounded-full bg-sky-950 px-3 py-1 text-xs font-semibold text-sky-300 border border-sky-800">
            <ShieldCheck className="h-4 w-4" /> Independent Pricing Assurance Layer
          </div>
          <h1 className="text-3xl font-extrabold text-white sm:text-5xl tracking-tight leading-tight">
            RateGuard verifies what your rating engine <span className="text-sky-400">actually does</span> — not just what it was configured to do.
          </h1>
          <p className="text-sm sm:text-base text-slate-300 leading-relaxed">
            PricingCenter and other rating platforms are where insurers author and configure rates. RateGuard is an
            independent assurance layer that verifies whether the deployed implementation matches the approved
            actuarial intent — before it reaches customers. RateGuard complements Guidewire, Duck Creek, legacy
            platforms, and custom rating engines; it does not replace them.
          </p>
          <div className="pt-2 flex flex-wrap items-center gap-4">
            <Link
              href="/missions/new"
              className="inline-flex items-center gap-2 rounded-lg bg-sky-600 px-5 py-2.5 text-sm font-bold text-white shadow-lg shadow-sky-600/30 hover:bg-sky-500 transition-all"
            >
              <Cpu className="h-4 w-4" /> Start Assurance Mission <ArrowRight className="h-4 w-4" />
            </Link>
            <Link
              href="/positioning"
              className="inline-flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-800 px-5 py-2.5 text-sm font-semibold text-slate-200 hover:bg-slate-750 transition-all"
            >
              How RateGuard Complements PricingCenter
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

      {/* Pricing platform vs RateGuard distinction */}
      <section className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="rounded-xl border border-slate-700 bg-slate-900 p-5 space-y-2">
          <div className="text-xs font-bold uppercase tracking-wider text-slate-400">The pricing platform</div>
          <p className="text-sm text-slate-200 leading-relaxed">
            Creates and deploys rates. PricingCenter, Duck Creek, a legacy engine, or a custom rating service is
            where actuaries and pricing teams author, configure, and ship rating logic.
          </p>
        </div>
        <div className="rounded-xl border border-sky-700 bg-sky-950/30 p-5 space-y-2">
          <div className="text-xs font-bold uppercase tracking-wider text-sky-300">RateGuard</div>
          <p className="text-sm text-slate-100 leading-relaxed">
            Independently verifies the deployed result — that what actually shipped matches the approved actuarial
            intent — and quantifies who would be affected before a single bill or renewal goes out.
          </p>
        </div>
      </section>

      {/* Feature Highlights Grid */}
      <section className="grid grid-cols-1 gap-6 sm:grid-cols-3">
        <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-5 space-y-2">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-sky-950 text-sky-400 border border-sky-800">
            <FileCode2 className="h-5 w-5" />
          </div>
          <h3 className="text-base font-bold text-white">Vendor-Neutral by Contract</h3>
          <p className="text-xs text-slate-400 leading-relaxed">
            Any REST rating engine that implements RateGuard&apos;s versioned connector contract can be verified —
            including an engine fronting Guidewire, Duck Creek, or a legacy platform. RateGuard has not built or
            tested a named-vendor adapter; the contract, not a proprietary integration, is what makes it
            vendor-neutral.
          </p>
        </div>

        <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-5 space-y-2">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-purple-950 text-purple-400 border border-purple-800">
            <Lock className="h-5 w-5" />
          </div>
          <h3 className="text-base font-bold text-white">Deterministic Math, Bounded AI</h3>
          <p className="text-xs text-slate-400 leading-relaxed">
            Gemini orchestrates and explains findings, but all factor lookup, calculation node ordering, and premium
            math are executed by deterministic Python code using exact <code className="text-sky-300 font-mono">Decimal</code> arithmetic.
          </p>
        </div>

        <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-5 space-y-2">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-emerald-950 text-emerald-400 border border-emerald-800">
            <Database className="h-5 w-5" />
          </div>
          <h3 className="text-base font-bold text-white">Bounded Synthetic Portfolio Impact</h3>
          <p className="text-xs text-slate-400 leading-relaxed">
            Quantifies affected-policy counts and dollar exposure across a seeded, synthetic 50,000-policy portfolio
            to support <code className="text-rose-300 font-mono font-bold">PASS</code> /
            <code className="text-rose-300 font-mono font-bold"> BLOCK_DEPLOYMENT</code> /
            <code className="text-amber-300 font-mono font-bold"> REVIEW_REQUIRED</code> decisions.
          </p>
        </div>
      </section>

      {/* Customer-facing narrative order */}
      <section className="space-y-4">
        <h2 className="text-xl font-bold text-white flex items-center gap-2">
          <ClipboardCheck className="h-5 w-5 text-sky-400" /> How a Verification Mission Runs
        </h2>
        <ol className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {NARRATIVE_STEPS.map((step) => (
            <li
              key={step.n}
              className="rounded-xl border border-slate-800 bg-slate-900/50 p-4 flex gap-3"
            >
              <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-sky-950 text-xs font-bold text-sky-300 border border-sky-800">
                {step.n}
              </div>
              <div>
                <div className="text-sm font-bold text-white">{step.title}</div>
                <div className="text-xs text-slate-400 mt-0.5 leading-relaxed">{step.desc}</div>
              </div>
            </li>
          ))}
        </ol>
      </section>

      {/* Ideal customer profile teaser */}
      <section className="rounded-xl border border-amber-800/60 bg-amber-950/20 p-5 space-y-2">
        <div className="flex items-center gap-2 text-sm font-bold text-amber-300">
          <ScaleIcon className="h-4 w-4" /> Who RateGuard is for
        </div>
        <p className="text-xs sm:text-sm text-slate-300 leading-relaxed">
          RateGuard delivers the most value to insurers running multiple rating engines, migrating between them,
          changing rates frequently, handing pricing logic across teams, or carrying meaningful market-conduct
          exposure. A carrier on a single, deeply trusted rating engine with infrequent rate changes may find
          RateGuard optional rather than essential. See the full{' '}
          <Link href="/positioning" className="underline text-amber-200 hover:text-amber-100">
            positioning and buyer-fit breakdown
          </Link>
          .
        </p>
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
