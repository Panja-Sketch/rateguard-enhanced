import Link from 'next/link';
import {
  ShieldCheck,
  Scale,
  Users,
  XCircle,
  CheckCircle2,
  GitBranch,
  FileWarning,
} from 'lucide-react';

const FRAMEWORK_POINTS = [
  {
    title: '1. Rate authoring vs. independent verification',
    body: 'PricingCenter (and other rating platforms) is where insurers author and configure rates: building rate tables, rules, and calculation logic, then deploying them. RateGuard does not author or configure rates. It independently verifies, after the fact, whether what was deployed matches what was approved — a separate function performed by a separate system.',
  },
  {
    title: '2. Approved intent vs. configured intent',
    body: "A rating platform can faithfully execute whatever was configured in it — that is its job. It cannot, by itself, prove that what was configured matches the actuarial filing or specification that was approved. RateGuard holds the approved specification (a controlled workbook or IPIR JSON) as a separate, independent reference and compares the platform's actual behavior against it.",
  },
  {
    title: '3. Multi-engine and migration reality',
    body: "Insurers run more than one rating engine at a time, and migrate between them. RateGuard reaches a candidate implementation through a vendor-neutral REST connector contract — any target that implements the contract can be registered and verified. This includes an engine fronting Guidewire, Duck Creek, or a legacy platform, but RateGuard has not built or tested a named-vendor adapter for any of them; the contract is what makes it vendor-neutral, not a proprietary integration.",
  },
  {
    title: '4. Segregation of duties',
    body: 'The team that builds and deploys pricing logic and the team that verifies it should not be the same function, using the same system, checking its own work. RateGuard is architecturally and operationally separate from the rating platform, which is the property regulators, auditors, and market-conduct reviewers look for.',
  },
  {
    title: '5. Customer-impact quantification',
    body: 'A structural code diff does not tell you how many customers would be overcharged or undercharged, or when. RateGuard runs a confirmed defect against a bounded, synthetic, seeded portfolio to produce affected-policy counts, overcharge/undercharge dollar amounts, and 30/60/90-day renewal timing — quantified customer impact, not just a technical finding.',
  },
  {
    title: '6. Evidence and accountability',
    body: 'Every mission produces a tamper-evident, SHA-256 hashed evidence bundle with a manifest — source hashes, connector request/response hashes, stage-by-stage outcomes, and the final decision — downloadable for release, compliance, and customer-protection review. Each artifact is independently hashed and listed in the manifest; the records are not cryptographically linked to one another in a chain.',
  },
  {
    title: '7. Deployment-independent release gate',
    body: 'The release decision — PASS, BLOCK_DEPLOYMENT, or REVIEW_REQUIRED — is produced the same way regardless of what kind of target the connector points at. Ambiguous or incomplete evidence, a connector failure or timeout, or an incompatible package always returns REVIEW_REQUIRED, never a silent PASS.',
  },
];

const COMPARISON_ROWS: Array<{ label: string; pricingCenter: string; rateguard: string }> = [
  {
    label: 'Rate authoring & configuration',
    pricingCenter: 'Yes — this is its core function',
    rateguard: 'No — RateGuard does not author or configure rates',
  },
  {
    label: 'Deployed-engine verification',
    pricingCenter: 'Not its function',
    rateguard: 'Yes — verifies the deployed implementation against approved intent',
  },
  {
    label: 'Approved-intent comparison',
    pricingCenter: 'Not applicable — executes whatever is configured',
    rateguard: 'Yes — compares against an independently held approved specification',
  },
  {
    label: 'Cross-engine / multi-vendor support',
    pricingCenter: 'Single platform',
    rateguard: 'Any REST target implementing the connector contract (contract-based, not a named-vendor adapter)',
  },
  {
    label: 'Portfolio customer-impact quantification',
    pricingCenter: 'Not its function',
    rateguard: 'Yes — bounded synthetic 50,000-policy portfolio impact analysis',
  },
  {
    label: 'Tamper-evident evidence bundle',
    pricingCenter: 'Not its function',
    rateguard: 'Yes — SHA-256 hashed evidence bundle with a manifest',
  },
  {
    label: 'Independent release decision',
    pricingCenter: 'Not its function',
    rateguard: 'Yes — PASS / BLOCK_DEPLOYMENT / REVIEW_REQUIRED',
  },
];

const ICP_FIT = [
  'Runs more than one rating engine, or is migrating from one to another',
  'Changes rates frequently, increasing the window for silent implementation drift',
  'Hands off pricing logic across separate actuarial, engineering, and platform teams',
  'Carries meaningful market-conduct or regulatory exposure from mispriced policies',
];

const NOT_DOES = [
  'Does not accept arbitrary Excel workbooks, macros, PDFs, filings, or an arbitrary codebase — only the exact RateGuard Controlled Workbook v1 contract and strict IPIR JSON.',
  'Does not have a built, tested adapter for Guidewire, Duck Creek, AS400, or any other named rating platform — only the vendor-neutral REST connector contract, which such a platform could sit behind once wired up.',
  'Does not make a legal fairness or discrimination determination. Its Impact Distribution Review screens configured synthetic cohorts for uneven outcomes; it is not a legal finding and does not replace actuarial, compliance, or legal review.',
  'Does not integrate with live production renewal or billing systems. All portfolio and pipeline impact analysis runs against a synthetic, de-identified, seeded 50,000-policy dataset, always disclosed as synthetic.',
  'Does not automatically send policyholder correspondence. It produces draft consumer explanations for authorized human review and approval only.',
  'Does not claim perfect accuracy, regulatory certification, or a legal-compliance guarantee for its results, and its evidence bundle is a SHA-256 hashed bundle with a manifest, not a cryptographically linked chain of records.',
];

export default function PositioningPage() {
  return (
    <div className="space-y-10 max-w-5xl mx-auto">
      <div>
        <h1 className="text-2xl sm:text-3xl font-extrabold text-white flex items-center gap-2">
          <ShieldCheck className="h-7 w-7 text-sky-400" /> How RateGuard Complements Rating Platforms and Engines
        </h1>
        <p className="text-sm text-slate-300 mt-3 leading-relaxed max-w-3xl">
          PricingCenter is where insurers author and configure rates. RateGuard is an independent assurance layer
          that verifies whether the deployed implementation matches the approved actuarial intent. RateGuard
          complements Guidewire, Duck Creek, legacy platforms, and custom rating engines; it does not replace them.
        </p>
      </div>

      {/* Framework */}
      <section className="space-y-4">
        <h2 className="text-lg font-bold text-white flex items-center gap-2">
          <GitBranch className="h-5 w-5 text-sky-400" /> The Positioning Framework
        </h2>
        <div className="grid grid-cols-1 gap-4">
          {FRAMEWORK_POINTS.map((p) => (
            <div key={p.title} className="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
              <div className="text-sm font-bold text-white">{p.title}</div>
              <p className="text-xs text-slate-400 mt-1.5 leading-relaxed">{p.body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Comparison Table */}
      <section className="space-y-4">
        <h2 className="text-lg font-bold text-white flex items-center gap-2">
          <CheckCircle2 className="h-5 w-5 text-sky-400" /> Complementary, Not Competing
        </h2>
        <p className="text-xs text-slate-400 max-w-3xl">
          This table describes two systems performing different functions in the same pricing lifecycle — it is not
          a claim that RateGuard outperforms or replaces PricingCenter or any rating platform.
        </p>
        <div className="overflow-x-auto rounded-xl border border-slate-800 bg-slate-900/80 shadow-xl">
          <table className="w-full text-left text-xs">
            <thead className="border-b border-slate-800 bg-slate-950 text-slate-400">
              <tr>
                <th className="px-4 py-3 font-medium">Capability</th>
                <th className="px-4 py-3 font-medium">PricingCenter (rating platform)</th>
                <th className="px-4 py-3 font-medium text-sky-300">RateGuard (assurance layer)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {COMPARISON_ROWS.map((row) => (
                <tr key={row.label}>
                  <td className="px-4 py-3 font-bold text-white">{row.label}</td>
                  <td className="px-4 py-3 text-slate-400">{row.pricingCenter}</td>
                  <td className="px-4 py-3 text-slate-200">{row.rateguard}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Ideal customer profile */}
      <section className="space-y-4">
        <h2 className="text-lg font-bold text-white flex items-center gap-2">
          <Users className="h-5 w-5 text-sky-400" /> Who Gets the Most Value
        </h2>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="rounded-xl border border-emerald-800/60 bg-emerald-950/20 p-4 space-y-2">
            <div className="text-sm font-bold text-emerald-300">Strongest fit</div>
            <ul className="space-y-1.5 text-xs text-slate-300">
              {ICP_FIT.map((line) => (
                <li key={line} className="flex gap-2">
                  <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-400 mt-0.5" />
                  <span>{line}</span>
                </li>
              ))}
            </ul>
          </div>
          <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-4 space-y-2">
            <div className="flex items-center gap-2 text-sm font-bold text-slate-300">
              <Scale className="h-4 w-4" /> Honest boundary
            </div>
            <p className="text-xs text-slate-400 leading-relaxed">
              A small insurer running a single, deeply trusted rating engine with infrequent rate changes and low
              regulatory exposure may reasonably see RateGuard as optional rather than essential — the operational
              conditions that make independent verification valuable (multiple engines, frequent change, team
              handoffs, market-conduct exposure) may simply not be present.
            </p>
          </div>
        </div>
      </section>

      {/* What RateGuard does not do */}
      <section className="space-y-4">
        <h2 className="text-lg font-bold text-white flex items-center gap-2">
          <XCircle className="h-5 w-5 text-rose-400" /> What RateGuard Does Not Do
        </h2>
        <div className="rounded-xl border border-rose-900/50 bg-rose-950/10 p-4">
          <ul className="space-y-2.5 text-xs text-slate-300">
            {NOT_DOES.map((line) => (
              <li key={line} className="flex gap-2">
                <FileWarning className="h-3.5 w-3.5 shrink-0 text-rose-400 mt-0.5" />
                <span>{line}</span>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <div className="pt-2">
        <Link href="/architecture" className="text-xs text-sky-300 underline hover:text-sky-200">
          See the vendor-neutral connector contract in the system architecture →
        </Link>
      </div>
    </div>
  );
}
