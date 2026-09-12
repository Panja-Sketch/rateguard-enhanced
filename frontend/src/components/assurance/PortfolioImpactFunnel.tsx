'use client';

import { PortfolioExposureResult } from '@/lib/types/assurance';
import { DollarSign, AlertCircle, TrendingDown, TrendingUp, Users, ShieldAlert, CheckCircle2, Layers } from 'lucide-react';

interface PortfolioImpactFunnelProps {
  portfolio?: (PortfolioExposureResult & {
    undercharge_amount?: string | number;
    overcharge_amount?: string | number;
    signed_variance?: string | number;
    absolute_variance?: string | number;
    semantically_exposed?: number;
    behaviorally_affected?: number;
    financially_affected?: number;
    overlapping_multi_defect_count?: number;
  }) | null;
  isCompleted?: boolean;
  // Symmetric Equivalence assumes neither source is authoritative -- use
  // neutral Source A/B framing instead of Intent/Target language.
  neutralLabels?: boolean;
}

export function PortfolioImpactFunnel({ portfolio, isCompleted = true, neutralLabels = false }: PortfolioImpactFunnelProps) {
  if (!portfolio) {
    return (
      <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-8 text-center text-slate-400">
        {isCompleted
          ? 'Portfolio blast radius analysis omitted or not available for this run.'
          : 'Waiting for 50,000 policy portfolio blast radius analysis...'}
      </div>
    );
  }

  const formatCurrency = (val?: string | number) => {
    if (val === undefined || val === null || val === '') return '$0.00';
    const num = typeof val === 'number' ? val : parseFloat(String(val));
    if (isNaN(num)) return '$0.00';
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(num);
  };

  const formatCount = (val?: number) => {
    if (val === undefined || val === null) return '0';
    return new Intl.NumberFormat('en-US').format(val);
  };

  const totalPolicies = portfolio.total_policies_analyzed ?? portfolio.total_policies ?? 50000;
  const exposedCount = portfolio.semantically_exposed_count ?? portfolio.exposed_policy_count ?? (portfolio as any).semantically_exposed ?? 0;
  const affectedCount = portfolio.financially_affected_count ?? (portfolio as any).financially_affected ?? 0;
  const behaviorCount = portfolio.behaviorally_affected_count ?? (portfolio as any).behaviorally_affected ?? affectedCount;

  const exposedPct = portfolio.exposed_policy_pct ?? (totalPolicies > 0 ? Number(((exposedCount / totalPolicies) * 100).toFixed(2)) : 0);
  const affectedPct = portfolio.financially_affected_pct ?? (totalPolicies > 0 ? Number(((affectedCount / totalPolicies) * 100).toFixed(2)) : 0);

  const absoluteVariance = portfolio.absolute_financial_exposure ?? portfolio.total_absolute_variance ?? (portfolio as any).absolute_variance ?? '0.00';
  const signedVariance = portfolio.signed_net_variance ?? portfolio.total_signed_variance ?? (portfolio as any).signed_variance ?? '0.00';

  const underchargeCount = portfolio.undercharged_policy_count ?? 0;
  const underchargeAmount = portfolio.total_undercharge_amount ?? (portfolio as any).undercharge_amount ?? '0.00';

  const overchargeCount = portfolio.overcharged_policy_count ?? 0;
  const overchargeAmount = portfolio.total_overcharge_amount ?? (portfolio as any).overcharge_amount ?? '0.00';

  const multiDefectCount = portfolio.multi_defect_policy_count ?? (portfolio as any).overlapping_multi_defect_count ?? 0;

  const isClean = affectedCount === 0 && parseFloat(String(absoluteVariance)) === 0;

  return (
    <div className="space-y-6">
      {/* Header Banner */}
      <div className={`rounded-xl border p-4 flex items-start gap-3 ${
        isClean
          ? 'border-emerald-800/60 bg-emerald-950/20 text-emerald-200'
          : 'border-sky-800/60 bg-sky-950/30 text-sky-200'
      }`}>
        {isClean ? (
          <CheckCircle2 className="h-5 w-5 text-emerald-400 shrink-0 mt-0.5" />
        ) : (
          <AlertCircle className="h-5 w-5 text-sky-400 shrink-0 mt-0.5" />
        )}
        <div className="text-xs">
          <span className="font-bold text-white">
            {isClean ? 'Clean Portfolio Verification: ' : '50,000-Policy Blast Radius Analysis: '}
          </span>
          {isClean
            ? 'Deterministic execution across the 50,000 policy portfolio confirmed $0.00 financial exposure and 0 affected policies.'
            : neutralLabels
              ? 'Evaluating Source A against Source B across synthetic risk distribution parameters.'
              : 'Evaluating pricing intent against target engine implementation across synthetic risk distribution parameters.'}
        </div>
      </div>

      {/* Blast Radius Visual Funnel */}
      <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-5 shadow-xl space-y-4">
        <h4 className="text-xs font-bold text-slate-300 uppercase tracking-wider flex items-center gap-2">
          <Layers className="h-4 w-4 text-sky-400" />
          Exposure & Defect Propagation Funnel
        </h4>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-4">
          <div className="rounded-xl border border-slate-800 bg-slate-950 p-4">
            <div className="flex items-center justify-between text-slate-400 text-xs mb-1">
              <span>1. Total Analyzed</span>
              <Users className="h-4 w-4 text-sky-400" />
            </div>
            <div className="text-2xl font-extrabold text-white font-mono">{formatCount(totalPolicies)}</div>
            <div className="text-[11px] text-slate-500 mt-0.5">Carrier In-Force Base</div>
          </div>

          <div className="rounded-xl border border-amber-900/50 bg-amber-950/20 p-4">
            <div className="flex items-center justify-between text-amber-300 text-xs mb-1">
              <span>2. Semantically Exposed</span>
              <ShieldAlert className="h-4 w-4 text-amber-400" />
            </div>
            <div className="text-2xl font-extrabold text-amber-400 font-mono">{formatCount(exposedCount)}</div>
            <div className="text-[11px] text-amber-300/70 mt-0.5">{exposedPct}% exposed to diffs</div>
          </div>

          <div className="rounded-xl border border-purple-900/50 bg-purple-950/20 p-4">
            <div className="flex items-center justify-between text-purple-300 text-xs mb-1">
              <span>3. Behaviorally Affected</span>
              <AlertCircle className="h-4 w-4 text-purple-400" />
            </div>
            <div className="text-2xl font-extrabold text-purple-400 font-mono">{formatCount(behaviorCount)}</div>
            <div className="text-[11px] text-purple-300/70 mt-0.5">Trace divergence reproduced</div>
          </div>

          <div className="rounded-xl border border-rose-900/50 bg-rose-950/20 p-4">
            <div className="flex items-center justify-between text-rose-300 text-xs mb-1">
              <span>4. Financially Affected</span>
              <DollarSign className="h-4 w-4 text-rose-400" />
            </div>
            <div className="text-2xl font-extrabold text-rose-400 font-mono">{formatCount(affectedCount)}</div>
            <div className="text-[11px] text-rose-300/70 mt-0.5">{affectedPct}% premium variance</div>
          </div>
        </div>
      </div>

      {/* Financial Exposure Breakdown */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="rounded-xl border border-rose-900/50 bg-rose-950/20 p-5 space-y-2">
          <div className="flex items-center justify-between">
            <h4 className="text-sm font-bold text-rose-300 flex items-center gap-2">
              <TrendingDown className="h-4 w-4 text-rose-400" />
              {neutralLabels ? 'Source B Lower Than Source A' : 'Carrier Undercharge (Revenue Leakage)'}
            </h4>
            <span className="font-mono text-xs font-bold text-rose-400">
              {formatCount(underchargeCount)} policies
            </span>
          </div>
          <div className="text-3xl font-extrabold text-rose-400 font-mono">
            {formatCurrency(underchargeAmount)}
          </div>
          {underchargeCount > 0 && (
            <p className="text-xs text-slate-400 leading-relaxed">
              {neutralLabels
                ? 'Absolute financial difference where Source B produced lower premiums than Source A.'
                : 'Target rating engine under-billed policyholders due to defective discounts or factor reductions.'}
            </p>
          )}
        </div>

        <div className="rounded-xl border border-amber-900/50 bg-amber-950/20 p-5 space-y-2">
          <div className="flex items-center justify-between">
            <h4 className="text-sm font-bold text-amber-300 flex items-center gap-2">
              <TrendingUp className="h-4 w-4 text-amber-400" />
              {neutralLabels ? 'Source B Higher Than Source A' : 'Policyholder Overcharge (Regulatory Risk)'}
            </h4>
            <span className="font-mono text-xs font-bold text-amber-400">
              {formatCount(overchargeCount)} policies
            </span>
          </div>
          <div className="text-3xl font-extrabold text-amber-400 font-mono">
            {formatCurrency(overchargeAmount)}
          </div>
          {overchargeCount > 0 && (
            <p className="text-xs text-slate-400 leading-relaxed">
              {neutralLabels
                ? 'Absolute financial difference where Source B produced higher premiums than Source A.'
                : 'Target engine charged higher than actuarial filing intent, triggering consumer compliance risk.'}
            </p>
          )}
        </div>
      </div>

      {/* Totals & Overlapping Multi-Defect Row */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-4">
          <div className="text-xs text-slate-400">Signed Net Variance</div>
          <div className="text-xl font-extrabold text-white font-mono mt-1">
            {formatCurrency(signedVariance)}
          </div>
        </div>

        <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-4">
          <div className="text-xs text-slate-400">Absolute Financial Exposure</div>
          <div className="text-xl font-extrabold text-rose-400 font-mono mt-1">
            {formatCurrency(absoluteVariance)}
          </div>
        </div>

        <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-4">
          <div className="text-xs text-slate-400">Overlapping Multi-Defect Policies</div>
          <div className="text-xl font-extrabold text-purple-400 font-mono mt-1">
            {formatCount(multiDefectCount)}
          </div>
        </div>
      </div>
    </div>
  );
}
