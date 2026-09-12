'use client';

import { useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { ApiError, createAssuranceMission, describeFetchError } from '@/lib/api/client';
import { ComparisonMode, ValidationIssue } from '@/lib/types/assurance';
import {
  Play,
  Cpu,
  Layers,
  ArrowRight,
  ShieldCheck,
  Sparkles,
} from 'lucide-react';

export default function NewMissionPage() {
  const router = useRouter();
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [loading, setLoading] = useState(false);

  const [mode, setMode] = useState<ComparisonMode>('RELEASE_CONFORMANCE');
  const [name, setName] = useState('Arizona HO3 Pricing Release Conformance');
  const [product, setProduct] = useState('AZ_HO3');
  const [jurisdiction, setJurisdiction] = useState('Arizona');
  const [effectivePeriodStart, setEffectivePeriodStart] = useState('2026-09-01');
  const [portfolioDataset, setPortfolioDataset] = useState('az_ho3_2026_synthetic_50k.csv');
  const [gatingPolicy, setGatingPolicy] = useState('STRICT_ZERO_DRIFT');

  // Source A & B
  const [sourceAId, setSourceAId] = useState('AZ_HO3_2026_09');
  const [sourceAName, setSourceAName] = useState('Arizona HO3 Actuarial Spec (Canonical Filing Intent)');

  const [sourceBId, setSourceBId] = useState('AZ_HO3_2026_09_DEFECTIVE');
  const [sourceBName, setSourceBName] = useState('Arizona HO3 Target Rating Engine Implementation');

  const [sampleTargetType, setSampleTargetType] = useState<'DEFECTIVE' | 'CLEAN'>('DEFECTIVE');

  const [error, setError] = useState<string | null>(null);
  const [fieldIssues, setFieldIssues] = useState<ValidationIssue[]>([]);

  // Neutral "Source A" / "Source B" labels for a symmetric Equivalence
  // comparison (neither source is authoritative); Release Conformance keeps
  // the intent-vs-target framing since one side genuinely is authoritative.
  const isEquivalence = mode === 'EQUIVALENCE';
  const sourceALabel = isEquivalence ? 'Source A' : 'Source A (Pricing Intent)';
  const sourceBLabel = isEquivalence ? 'Source B' : 'Source B (Target Rating Implementation)';

  // Field Level Validation Rules
  const validateCurrentStep = () => {
    if (step === 1) return true;
    if (step === 2) {
      return !!(sourceAId && sourceBId);
    }
    if (step === 3) {
      return !!(name.trim() && product.trim() && jurisdiction.trim() && effectivePeriodStart.trim());
    }
    return true;
  };

  // This wizard only ever offers bundled sample sources (there is no real
  // upload picker here — that lives on /sources), so every mission created
  // through it is always explicitly marked as a demo sample.
  const isDemoSampleMission = true;

  // Guards against a double submission racing ahead of React's re-render of
  // the button's `disabled={loading}` prop (e.g. a fast double-click before
  // the first click's setLoading(true) has painted) — a ref is synchronous,
  // unlike state, so the second call is rejected immediately.
  const submitInFlightRef = useRef(false);

  const handleStartMission = async () => {
    if (submitInFlightRef.current) return;
    submitInFlightRef.current = true;
    setLoading(true);
    setError(null);
    setFieldIssues([]);
    try {
      const sourceB = {
        source_id: sampleTargetType === 'CLEAN' ? 'AZ_HO3_2026_09_CLEAN' : sourceBId,
        source_type: 'SAMPLE_RELEASE',
        name: sampleTargetType === 'CLEAN' ? 'Clean Compliant Target' : sourceBName,
      };

      const payload = {
        name,
        mode,
        product,
        jurisdiction,
        effective_period_start: effectivePeriodStart,
        portfolio_dataset: portfolioDataset,
        gating_policy: gatingPolicy,
        source_a: {
          source_id: sourceAId,
          source_type: 'SAMPLE_RELEASE',
          name: sourceAName,
        },
        source_b: sourceB,
        disposable_sample_run: true,
        is_demo_sample: isDemoSampleMission,
      };

      const res = await createAssuranceMission(payload);
      if (res.mission_id) {
        router.push(`/missions/${res.mission_id}`);
      }
    } catch (err: unknown) {
      // Structured 422 validation errors must render inline, not crash the page.
      if (err instanceof ApiError && err.issues && err.issues.length > 0) {
        setFieldIssues(err.issues);
        setError(null);
      } else {
        setError(describeFetchError(err, 'No mission was created.'));
      }
      submitInFlightRef.current = false;
      setLoading(false);
    }
  };

  const isFormValid = validateCurrentStep();

  return (
    <div className="space-y-8 max-w-4xl mx-auto">
      {/* Header */}
      <div>
        <div className="inline-flex items-center gap-2 rounded-full bg-sky-950 px-3 py-1 text-xs font-semibold text-sky-300 border border-sky-800 mb-2">
          <Cpu className="h-4 w-4" /> Assurance Mission V2 Configuration
        </div>
        <h1 className="text-2xl sm:text-3xl font-extrabold text-white">Start Assurance Mission</h1>
        <p className="text-xs sm:text-sm text-slate-400 mt-1">
          Configure an autonomous pricing release assurance mission with symmetric equivalence or release conformance.
        </p>
      </div>

      {error && (
        <div className="rounded-xl border border-rose-800 bg-rose-950/50 p-4 text-xs text-rose-300 font-mono">
          [Validation / Mission Error] {error}
        </div>
      )}

      {fieldIssues.length > 0 && (
        <div className="rounded-xl border border-rose-800 bg-rose-950/50 p-4 text-xs text-rose-300 space-y-1.5 font-mono">
          <div className="font-bold font-sans">Mission validation failed:</div>
          {fieldIssues.map((issue, idx) => (
            <div key={idx}>
              <span className="text-rose-400 font-bold">{issue.field}</span>
              <span className="text-rose-500"> [{issue.code}]</span>: {issue.message}
            </div>
          ))}
        </div>
      )}

      {/* Stepper Navigation Bar */}
      <div className="grid grid-cols-3 gap-3">
        <button
          onClick={() => setStep(1)}
          className={`rounded-xl border p-3.5 text-left text-xs transition-all ${
            step === 1
              ? 'border-sky-500 bg-sky-950/40 text-sky-300 font-bold'
              : 'border-slate-800 bg-slate-900/60 text-slate-400'
          }`}
        >
          <div className="text-[10px] font-mono uppercase text-slate-500 mb-0.5">Step 1</div>
          <div className="text-sm font-bold text-white">1. Comparison Mode</div>
        </button>

        <button
          onClick={() => setStep(2)}
          className={`rounded-xl border p-3.5 text-left text-xs transition-all ${
            step === 2
              ? 'border-sky-500 bg-sky-950/40 text-sky-300 font-bold'
              : 'border-slate-800 bg-slate-900/60 text-slate-400'
          }`}
        >
          <div className="text-[10px] font-mono uppercase text-slate-500 mb-0.5">Step 2</div>
          <div className="text-sm font-bold text-white">2. Configure Sources</div>
        </button>

        <button
          onClick={() => setStep(3)}
          className={`rounded-xl border p-3.5 text-left text-xs transition-all ${
            step === 3
              ? 'border-sky-500 bg-sky-950/40 text-sky-300 font-bold'
              : 'border-slate-800 bg-slate-900/60 text-slate-400'
          }`}
        >
          <div className="text-[10px] font-mono uppercase text-slate-500 mb-0.5">Step 3</div>
          <div className="text-sm font-bold text-white">3. Mission Config</div>
        </button>
      </div>

      {/* STEP 1: Comparison Mode Selection */}
      {step === 1 && (
        <div className="space-y-4">
          <h2 className="text-base font-bold text-white">Select Assurance Mission Comparison Mode</h2>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {/* Mode 1: Release Conformance */}
            <div
              onClick={() => {
                setMode('RELEASE_CONFORMANCE');
                setName('Arizona HO3 Pricing Release Conformance');
              }}
              className={`cursor-pointer rounded-2xl border p-5 space-y-3 transition-all ${
                mode === 'RELEASE_CONFORMANCE'
                  ? 'border-sky-500 bg-sky-950/40 shadow-xl shadow-sky-950/50'
                  : 'border-slate-800 bg-slate-900/60 hover:border-slate-700'
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="rounded bg-sky-950 px-2 py-0.5 text-[10px] font-bold text-sky-300 border border-sky-800">
                  Authoritative Intent
                </span>
                <ShieldCheck className="h-5 w-5 text-sky-400" />
              </div>
              <h3 className="text-sm font-bold text-white">Release Conformance</h3>
              <p className="text-xs text-slate-400 leading-relaxed">
                Determines whether a target rating engine implementation conforms strictly to an approved actuarial filing intent AST.
              </p>
            </div>

            {/* Mode 2: Equivalence */}
            <div
              onClick={() => {
                setMode('EQUIVALENCE');
                setName('Symmetric Pricing Model Equivalence');
              }}
              className={`cursor-pointer rounded-2xl border p-5 space-y-3 transition-all ${
                mode === 'EQUIVALENCE'
                  ? 'border-emerald-500 bg-emerald-950/40 shadow-xl shadow-emerald-950/50'
                  : 'border-slate-800 bg-slate-900/60 hover:border-slate-700'
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="rounded bg-emerald-950 px-2 py-0.5 text-[10px] font-bold text-emerald-300 border border-emerald-800">
                  Symmetric Comparison
                </span>
                <Layers className="h-5 w-5 text-emerald-400" />
              </div>
              <h3 className="text-sm font-bold text-white">Equivalence Mode</h3>
              <p className="text-xs text-slate-400 leading-relaxed">
                Compares Source A and Source B symmetrically to verify full AST behavioral equivalence in both directions (A vs B and B vs A) — neither source is assumed authoritative.
              </p>
            </div>
          </div>

          <div className="pt-4 flex justify-end">
            <button
              onClick={() => setStep(2)}
              className="inline-flex items-center gap-2 rounded-xl bg-sky-600 px-6 py-2.5 text-xs font-bold text-white hover:bg-sky-500 transition-all"
            >
              Continue to Step 2 <ArrowRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}

      {/* STEP 2: Configure Sources */}
      {step === 2 && (
        <div className="space-y-6">
          <h2 className="text-base font-bold text-white">Configure Pricing Sources</h2>

          <div className="space-y-3">
            <div className="flex items-center gap-2 rounded-lg border border-amber-800/60 bg-amber-950/20 px-3 py-2 text-xs text-amber-200">
              <Sparkles className="h-3.5 w-3.5 shrink-0" />
              <span>
                <strong className="font-bold">Demo sample.</strong> This wizard only offers RateGuard&apos;s bundled
                Arizona HO3 sample sources. To compare your own uploaded filings or engine configs, use the{' '}
                <a href="/sources" className="underline hover:text-amber-100">Sources</a> page instead.
              </span>
            </div>
            <div className="grid grid-cols-1 gap-6 sm:grid-cols-2">
            {/* Source A */}
            <div className="rounded-xl border border-sky-800/80 bg-slate-900/80 p-5 space-y-3">
              <div className="flex justify-between items-center">
                <span className="text-xs font-bold text-sky-300 uppercase tracking-wider">{sourceALabel}</span>
                <span className="text-xs text-slate-400 font-mono">AZ_HO3_2026_09</span>
              </div>
              <div className="text-sm font-bold text-white">Arizona HO3 Actuarial Spec</div>
              <p className="text-xs text-slate-400">
                {isEquivalence ? 'Rate Filing Specification (2026.09) — an alignment reference, not presumed authoritative' : 'Authoritative Rate Filing Specification (2026.09)'}
              </p>
            </div>

            {/* Source B Target Selection */}
            <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-5 space-y-3">
              <div className="flex justify-between items-center">
                <span className="text-xs font-bold text-purple-300 uppercase tracking-wider">{sourceBLabel}</span>
              </div>

              <div className="space-y-2">
                <label className="text-xs text-slate-400">{isEquivalence ? 'Select Source B Variant:' : 'Select Target Implementation Variant:'}</label>
                <div className="space-y-2">
                  <button
                    type="button"
                    onClick={() => setSampleTargetType('DEFECTIVE')}
                    className={`w-full rounded-lg border p-3 text-left text-xs transition-all ${
                      sampleTargetType === 'DEFECTIVE'
                        ? 'border-rose-500 bg-rose-950/30 text-rose-200 font-bold'
                        : 'border-slate-800 bg-slate-950 text-slate-400'
                    }`}
                  >
                    <div className="font-bold text-white">Target Implementation (Sample Release)</div>
                    <div className="text-[11px] text-slate-400 mt-0.5">Loads target engine implementation release candidate.</div>
                  </button>

                  <button
                    type="button"
                    onClick={() => setSampleTargetType('CLEAN')}
                    className={`w-full rounded-lg border p-3 text-left text-xs transition-all ${
                      sampleTargetType === 'CLEAN'
                        ? 'border-emerald-500 bg-emerald-950/30 text-emerald-200 font-bold'
                        : 'border-slate-800 bg-slate-950 text-slate-400'
                    }`}
                  >
                    <div className="font-bold text-white">Clean Control Release</div>
                    <div className="text-[11px] text-slate-400 mt-0.5">Loads clean, compliant target engine release.</div>
                  </button>
                </div>
              </div>
            </div>
            </div>
          </div>

          <div className="pt-4 flex justify-between">
            <button
              onClick={() => setStep(1)}
              className="rounded-xl border border-slate-800 bg-slate-900 px-5 py-2 text-xs font-semibold text-slate-300"
            >
              Back to Step 1
            </button>
            <button
              onClick={() => setStep(3)}
              disabled={!isFormValid}
              className="inline-flex items-center gap-2 rounded-xl bg-sky-600 px-6 py-2.5 text-xs font-bold text-white hover:bg-sky-500 transition-all disabled:opacity-50"
            >
              Continue to Step 3 <ArrowRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}

      {/* STEP 3: Mission Configuration & Launch */}
      {step === 3 && (
        <div className="space-y-6">
          <h2 className="text-base font-bold text-white">Configure Mission Scope & Gating Policy</h2>

          <div className="rounded-2xl border border-slate-800 bg-slate-900/80 p-6 space-y-4 shadow-xl">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div className="space-y-1">
                <label className="text-xs font-bold text-slate-300">Mission Name *</label>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-xs text-white focus:border-sky-500 focus:outline-none"
                />
              </div>

              <div className="space-y-1">
                <label className="text-xs font-bold text-slate-300">Insurance Product *</label>
                <input
                  type="text"
                  value={product}
                  onChange={(e) => setProduct(e.target.value)}
                  className="w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-xs text-white focus:border-sky-500 focus:outline-none"
                />
              </div>

              <div className="space-y-1">
                <label className="text-xs font-bold text-slate-300">Regulatory Jurisdiction *</label>
                <input
                  type="text"
                  value={jurisdiction}
                  onChange={(e) => setJurisdiction(e.target.value)}
                  className="w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-xs text-white focus:border-sky-500 focus:outline-none"
                />
              </div>

              <div className="space-y-1">
                <label className="text-xs font-bold text-slate-300">Effective Period Start *</label>
                <input
                  type="date"
                  value={effectivePeriodStart}
                  onChange={(e) => setEffectivePeriodStart(e.target.value)}
                  className="w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-xs font-mono text-white focus:border-sky-500 focus:outline-none"
                />
              </div>

              <div className="space-y-1">
                <label className="text-xs font-bold text-slate-300">Portfolio Dataset</label>
                <select
                  value={portfolioDataset}
                  onChange={(e) => setPortfolioDataset(e.target.value)}
                  className="w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-xs text-white focus:border-sky-500 focus:outline-none"
                >
                  <option value="az_ho3_2026_synthetic_50k.csv">50,000 Synthetic Policies (Authoritative Benchmark)</option>
                </select>
              </div>

              <div className="space-y-1">
                <label className="text-xs font-bold text-slate-300">Release Gating Policy</label>
                <select
                  value={gatingPolicy}
                  onChange={(e) => setGatingPolicy(e.target.value)}
                  className="w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-xs text-white focus:border-sky-500 focus:outline-none"
                >
                  <option value="STRICT_ZERO_DRIFT">STRICT ZERO DRIFT (Block any financial variance)</option>
                  <option value="TOLERATE_LOW_RISK">TOLERATE LOW RISK (Allow minor non-financial diffs)</option>
                </select>
              </div>
            </div>
          </div>

          <div className="pt-4 flex justify-between items-center">
            <button
              onClick={() => setStep(2)}
              className="rounded-xl border border-slate-800 bg-slate-900 px-5 py-2 text-xs font-semibold text-slate-300"
            >
              Back to Step 2
            </button>

            <button
              onClick={handleStartMission}
              disabled={loading || !isFormValid}
              className="inline-flex items-center gap-2 rounded-xl bg-gradient-to-r from-sky-600 to-purple-600 px-8 py-3 text-xs font-bold text-white hover:opacity-90 transition-all shadow-xl shadow-sky-950/50 disabled:opacity-50"
            >
              <Play className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
              {loading ? 'Executing Assurance Mission...' : 'Execute Assurance Mission'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
