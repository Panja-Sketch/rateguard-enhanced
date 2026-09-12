'use client';

import { useEffect, useState, useCallback, useRef } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import {
  getAssuranceMission,
  getAssuranceRunEvents,
  getAssuranceRunEvidence,
  cancelAssuranceMission,
  retryAssuranceMission,
  generateAlignmentOptions,
  AlignmentOptionsResult,
  ApiError,
} from '@/lib/api/client';
import { AssuranceMissionDetail, AssuranceResultV2, EvidenceRecord, WorkflowEvent } from '@/lib/types/assurance';
import { MissionErrorBoundary } from '@/components/assurance/MissionErrorBoundary';
import { SemanticDiffViewer } from '@/components/assurance/SemanticDiffViewer';
import { ImpactGraph } from '@/components/assurance/ImpactGraph';
import { TestPlanViewer } from '@/components/assurance/TestPlanViewer';
import { ReconciliationTrace } from '@/components/assurance/ReconciliationTrace';
import { PortfolioImpactFunnel } from '@/components/assurance/PortfolioImpactFunnel';
import { EvidenceLineage } from '@/components/assurance/EvidenceLineage';
import { AgentActivityPanel } from '@/components/assurance/AgentActivityPanel';
import {
  ShieldAlert,
  CheckCircle2,
  AlertTriangle,
  RefreshCw,
  GitCompare,
  Sparkles,
  Database,
  Bot,
  BarChart3,
  Layers,
  Check,
  TrendingDown,
  Clock,
  ArrowLeft,
  ArrowRight,
  Sliders,
  Ban,
  RotateCcw,
} from 'lucide-react';

const STAGE_ORDER = [
  'VALIDATION',
  'SEMANTIC_ANALYSIS',
  'IMPACT_ANALYSIS',
  'RISK_DIRECTED_TESTING',
  'RECONCILIATION',
  'PORTFOLIO_ANALYSIS',
  'REMEDIATION',
  'DECISION',
] as const;

type StageDisplayState = 'NOT_STARTED' | 'RUNNING' | 'DONE' | 'CANCELLED';

export default function MissionDetailPage() {
  const params = useParams();
  const missionId = params.missionId as string;

  const [missionData, setMissionData] = useState<AssuranceMissionDetail | null>(null);
  const [events, setEvents] = useState<WorkflowEvent[]>([]);
  const [evidence, setEvidence] = useState<EvidenceRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [consecutiveErrors, setConsecutiveErrors] = useState(0);

  const [cancelling, setCancelling] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [showCancelConfirm, setShowCancelConfirm] = useState(false);
  // In symmetric Equivalence mode, neither source is authoritative, so no
  // directional patch is computed during the mission run at all. A human
  // must explicitly pick which source to treat as the reference; only then
  // is the patch generated on demand (POST /missions/{id}/alignment-options).
  const [alignmentReference, setAlignmentReference] = useState<'A' | 'B' | null>(null);
  const [alignmentOptions, setAlignmentOptions] = useState<AlignmentOptionsResult | null>(null);
  const [alignmentLoading, setAlignmentLoading] = useState(false);
  const [alignmentError, setAlignmentError] = useState<string | null>(null);

  const chooseAlignmentReference = useCallback(async (reference: 'A' | 'B') => {
    setAlignmentReference(reference);
    setAlignmentLoading(true);
    setAlignmentError(null);
    try {
      const result = await generateAlignmentOptions(missionId, reference);
      setAlignmentOptions(result);
    } catch (err) {
      setAlignmentError(err instanceof ApiError ? err.message : 'Failed to generate alignment options.');
    } finally {
      setAlignmentLoading(false);
    }
  }, [missionId]);

  const [activeTab, setActiveTab] = useState<
    'summary' | 'semantic' | 'impact' | 'experiments' | 'recon' | 'blast' | 'remediation' | 'evidence' | 'agent'
  >('summary');

  const pollTimerRef = useRef<NodeJS.Timeout | null>(null);

  const loadData = useCallback(async () => {
    try {
      setError(null);
      let attempts = 0;
      let lastErr: any = null;
      while (attempts < 3) {
        try {
          const data = await getAssuranceMission(missionId);
          setMissionData(data);
          setConsecutiveErrors(0);
          // Real per-stage event history drives stage display below. A failure
          // fetching events must not blank the page — mission data already loaded.
          try {
            const eventsRes = await getAssuranceRunEvents(missionId);
            setEvents(eventsRes.events || []);
          } catch {
            // Non-fatal: stage display falls back to "not started" for all stages.
          }
          try {
            const evidenceRes = await getAssuranceRunEvidence(missionId);
            setEvidence(evidenceRes.evidence || []);
          } catch {
            // Non-fatal: the Evidence Lineage tab falls back to its empty state.
          }
          return;
        } catch (err) {
          lastErr = err;
          attempts++;
          if (attempts < 3) {
            await new Promise((resolve) => setTimeout(resolve, 400));
          }
        }
      }
      setConsecutiveErrors((prev) => prev + 1);
      if (lastErr instanceof ApiError) {
        setError(`API Error (${lastErr.message})`);
      } else {
        setError(lastErr instanceof Error ? lastErr.message : String(lastErr));
      }
    } finally {
      setLoading(false);
    }
  }, [missionId]);

  // Active Polling Loop (Interval 2.5s)
  useEffect(() => {
    loadData();

    return () => {
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
      }
    };
  }, [loadData]);

  useEffect(() => {
    if (!missionData) return;

    const currentStatus = (missionData.status || '').toUpperCase();
    const isTerminal = ['COMPLETED', 'FAILED', 'NEEDS_REVIEW', 'CANCELLED', 'ARCHIVED'].includes(currentStatus);

    if (!isTerminal && consecutiveErrors < 5) {
      pollTimerRef.current = setInterval(() => {
        loadData();
      }, 2500);
    } else {
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
      }
    }

    return () => {
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
      }
    };
  }, [missionData, consecutiveErrors, loadData]);

  if (loading && !missionData) {
    return (
      <div className="py-20 text-center space-y-3">
        <RefreshCw className="h-8 w-8 text-sky-400 animate-spin mx-auto" />
        <div className="text-sm font-bold text-white">Loading Assurance Mission Details...</div>
      </div>
    );
  }

  if (error && !missionData) {
    return (
      <div className="max-w-4xl mx-auto py-10 space-y-4">
        <div className="rounded-xl border border-rose-800 bg-rose-950/50 p-6 text-xs font-mono text-rose-300">
          [Mission Error] {error}
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={loadData}
            className="inline-flex items-center gap-1.5 rounded-lg bg-sky-600 px-4 py-2 text-xs font-bold text-white hover:bg-sky-500"
          >
            <RefreshCw className="h-4 w-4" /> Retry Polling
          </button>
          <Link
            href="/missions"
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-4 py-2 text-xs font-semibold text-slate-300 hover:bg-slate-800"
          >
            <ArrowLeft className="h-4 w-4" /> Back to History
          </Link>
        </div>
      </div>
    );
  }

  const result = (missionData?.result || {}) as unknown as AssuranceResultV2;
  const meta = (missionData?.metadata || {}) as Record<string, any>;
  const statusStr = (missionData?.status || 'QUEUED').toString().toUpperCase();
  const isRunning = ['QUEUED', 'RUNNING', 'VALIDATING', 'WAITING_RETRY'].includes(statusStr);
  const isCancelled = statusStr === 'CANCELLED';
  const decision = result?.release_decision?.data?.status || missionData?.decision || 'UNKNOWN';
  const eligibleActions = missionData?.eligible_actions || { cancel: false, retry: false, archive: false, delete: false };
  const currentStage = missionData?.current_stage || null;
  // Symmetric Equivalence assumes neither source is authoritative -- every
  // tab consistently uses neutral "Source A"/"Source B" labels instead of
  // Release Conformance's Intent/Target framing.
  const isEquivalence = meta.mode === 'EQUIVALENCE';

  // Section Safeties
  const validationSec = result?.validation || { status: 'NOT_RUN' };
  const semanticSec = result?.semantic_analysis || { status: 'NOT_RUN' };
  const impactSec = result?.impact_analysis || { status: 'NOT_RUN' };
  const experimentsSec = result?.experiments || { status: 'NOT_RUN' };
  const reconSec = result?.reconciliation || { status: 'NOT_RUN' };
  const blastSec = result?.blast_radius || { status: 'NOT_RUN' };
  const remediationSec = result?.remediation || { status: 'NOT_RUN' };
  const revalidationSec = result?.revalidation || { status: 'NOT_RUN' };
  const agentSec = result?.agent_execution || { status: 'NOT_RUN' };

  // Heartbeat threshold check (> 120s) — reported honestly as "no heartbeat", not
  // as a guess about what the worker might currently be doing.
  const lastUpdated = missionData?.updated_at ? new Date(missionData.updated_at).getTime() : Date.now();
  const isStale = isRunning && Date.now() - lastUpdated > 120000;

  // Real per-stage state: "not started" until an actual stage-start event exists,
  // "running" only for the single stage the backend reports as current_stage, and
  // never "running" once the mission itself is CANCELLED.
  const stageState = (stageName: (typeof STAGE_ORDER)[number]): StageDisplayState => {
    if (isCancelled) return 'CANCELLED';
    const hasEvent = events.some((e) => e.stage === stageName);
    if (!hasEvent) return 'NOT_STARTED';
    if (currentStage === stageName && isRunning) return 'RUNNING';
    return 'DONE';
  };

  const stageStatusLine = (stageName: (typeof STAGE_ORDER)[number], sec: { status?: string; reason?: string | null }) => {
    if (sec?.status === 'SUCCEEDED') return null; // real component renders instead
    const state = stageState(stageName);
    if (state === 'CANCELLED') return 'Cancelled — mission execution was stopped before this stage completed.';
    if (state === 'RUNNING') return 'Running.';
    if (state === 'NOT_STARTED') {
      // Once the mission itself is done, "Not started" is misleading -- a
      // stage with no event ever fired either finished (a clean run whose 0
      // diffs meant DAG/RCA/remediation were never needed) or the mission
      // ended (failed/cancelled) before reaching it. Neither is "not started
      // yet" from a user's perspective once nothing more is going to happen.
      if (!isRunning) {
        return statusStr === 'COMPLETED'
          ? 'Skipped by design — no semantic differences required DAG, RCA, or remediation analysis.'
          : 'Not reached — mission ended before this stage began.';
      }
      return 'Not started.';
    }
    return sec?.reason || (sec?.status === 'FAILED' ? 'Failed.' : 'Completed without producing this section.');
  };

  const handleCancel = async () => {
    setCancelling(true);
    setActionError(null);
    try {
      await cancelAssuranceMission(missionId);
      setShowCancelConfirm(false);
      await loadData();
    } catch (err: unknown) {
      setActionError(err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err));
    } finally {
      setCancelling(false);
    }
  };

  const handleRetry = async () => {
    setRetrying(true);
    setActionError(null);
    try {
      await retryAssuranceMission(missionId);
      await loadData();
    } catch (err: unknown) {
      setActionError(err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err));
    } finally {
      setRetrying(false);
    }
  };

  return (
    <MissionErrorBoundary onRetry={loadData}>
      <div className="space-y-8 max-w-6xl mx-auto">
        {/* Header Bar */}
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between border-b border-slate-800 pb-4">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-extrabold text-white font-mono">{missionId}</h1>
              <span className={`rounded px-2.5 py-0.5 text-xs font-mono font-bold border ${
                isRunning ? 'bg-sky-950 text-sky-300 border-sky-800 animate-pulse' : 'bg-slate-800 text-slate-200 border-slate-700'
              }`}>
                {statusStr}
              </span>
              <span className="rounded bg-purple-950 px-2 py-0.5 text-xs font-mono font-bold text-purple-300 border border-purple-800">
                {meta.mode || 'RELEASE_CONFORMANCE'}
              </span>
            </div>
            <p className="text-xs text-slate-400 mt-1">
              {meta.name || 'Autonomous Pricing Release Assurance Mission'}
            </p>
          </div>

          <div className="flex items-center gap-2">
            {eligibleActions.retry && (
              <button
                onClick={handleRetry}
                disabled={retrying}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800 px-3.5 py-1.5 text-xs font-semibold text-slate-200 hover:bg-slate-700 disabled:opacity-50"
              >
                <RotateCcw className={`h-3.5 w-3.5 ${retrying ? 'animate-spin' : ''}`} /> Retry
              </button>
            )}
            {eligibleActions.cancel && (
              <button
                onClick={() => setShowCancelConfirm(true)}
                className="inline-flex items-center gap-1.5 rounded-lg border border-amber-800 bg-amber-950 px-3.5 py-1.5 text-xs font-semibold text-amber-300 hover:bg-amber-900"
              >
                <Ban className="h-3.5 w-3.5" /> Cancel
              </button>
            )}
            <button
              onClick={loadData}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-800 bg-slate-900 px-3.5 py-1.5 text-xs font-semibold text-slate-300 hover:bg-slate-850"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${isRunning ? 'animate-spin' : ''}`} /> Refresh
            </button>
          </div>
        </div>

        {actionError && (
          <div className="rounded-xl border border-rose-800 bg-rose-950/50 p-4 text-xs text-rose-300 font-mono">
            [Action Failed] {actionError}
          </div>
        )}

        {/* Cancel Confirmation */}
        {showCancelConfirm && (
          <div className="rounded-xl border border-amber-800 bg-amber-950/30 p-4 space-y-3">
            <p className="text-xs text-amber-200">
              {statusStr === 'RUNNING'
                ? 'This mission is actively running — cancellation will be requested and honored cooperatively between stages, not instantly.'
                : 'Cancel this mission? It will transition directly to CANCELLED.'}
            </p>
            <div className="flex items-center gap-2">
              <button
                onClick={handleCancel}
                disabled={cancelling}
                className="rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-amber-500 disabled:opacity-50"
              >
                {cancelling ? 'Cancelling...' : 'Confirm Cancel'}
              </button>
              <button
                onClick={() => setShowCancelConfirm(false)}
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs font-semibold text-slate-300 hover:bg-slate-800"
              >
                Dismiss
              </button>
            </div>
          </div>
        )}

        {/* Stale Mission Heartbeat Warning Banner — reports the fact, not a guess */}
        {isStale && (
          <div className="rounded-xl border border-amber-800 bg-amber-950/40 p-4 text-xs text-amber-200 flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Clock className="h-4 w-4 text-amber-400 flex-shrink-0" />
              <span>No heartbeat received from the worker for over 120 seconds. Current stage: {currentStage || 'unknown'}.</span>
            </div>
            <button
              onClick={loadData}
              className="rounded bg-amber-900 px-3 py-1 text-xs font-bold text-amber-100 border border-amber-700 hover:bg-amber-800"
            >
              Retry Polling
            </button>
          </div>
        )}

        {/* Status Banner: one honest message per lifecycle state, no fabricated progress text */}
        {statusStr === 'QUEUED' && (
          <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-5 flex items-center gap-2 text-sm text-slate-300">
            <Clock className="h-4 w-4 text-slate-400" />
            Waiting for an assurance worker.
          </div>
        )}
        {statusStr === 'VALIDATING' && (
          <div className="rounded-xl border border-sky-800/80 bg-sky-950/30 p-5 flex items-center gap-2 text-sm text-sky-300">
            <RefreshCw className="h-4 w-4 animate-spin text-sky-400" />
            Validating mission sources and connector configuration.
          </div>
        )}
        {statusStr === 'RUNNING' && (
          <div className="rounded-xl border border-sky-800/80 bg-sky-950/30 p-5 space-y-1">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-sky-300 font-bold text-sm">
                <RefreshCw className="h-4 w-4 animate-spin text-sky-400" />
                Running{currentStage ? `: ${currentStage.replaceAll('_', ' ').toLowerCase()}` : ''}
              </div>
              {missionData?.cancellation_requested && (
                <span className="font-mono text-xs text-amber-400">Cancellation requested — stopping at next checkpoint</span>
              )}
            </div>
          </div>
        )}
        {statusStr === 'WAITING_RETRY' && (
          <div className="rounded-xl border border-amber-800/80 bg-amber-950/30 p-5 space-y-1 text-sm text-amber-200">
            <div className="font-bold">Waiting to retry (attempt {missionData?.attempt_number ?? 1}).</div>
            {missionData?.status_reason && <p className="text-xs text-amber-300/90">{missionData.status_reason}</p>}
          </div>
        )}
        {statusStr === 'FAILED' && (
          <div className="rounded-xl border border-rose-800/80 bg-rose-950/30 p-5 space-y-1 text-sm text-rose-200">
            <div className="font-bold">Failed{currentStage ? ` at stage: ${currentStage.replaceAll('_', ' ').toLowerCase()}` : ''}.</div>
            {missionData?.status_reason && <p className="text-xs text-rose-300/90 font-mono">{missionData.status_reason}</p>}
          </div>
        )}
        {isCancelled && (
          <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-5 text-sm text-slate-300">
            Cancelled. {missionData?.status_reason || 'Execution was stopped before completion.'}
          </div>
        )}

        {/* Release Decision Banner */}
        {result?.release_decision?.data && (
          <div
            className={`rounded-2xl border p-6 shadow-2xl transition-all ${
              decision === 'BLOCK_DEPLOYMENT'
                ? 'border-rose-800/80 bg-rose-950/40 text-rose-100'
                : decision === 'PASS'
                ? 'border-emerald-800/80 bg-emerald-950/40 text-emerald-100'
                : 'border-amber-800/80 bg-amber-950/40 text-amber-100'
            }`}
          >
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between border-b border-white/10 pb-4 mb-4">
              <div className="flex items-center gap-3">
                <div
                  className={`flex h-12 w-12 items-center justify-center rounded-xl border shadow-lg ${
                    decision === 'BLOCK_DEPLOYMENT'
                      ? 'bg-rose-900 text-rose-200 border-rose-700'
                      : decision === 'PASS'
                      ? 'bg-emerald-900 text-emerald-200 border-emerald-700'
                      : 'bg-amber-900 text-amber-200 border-amber-700'
                  }`}
                >
                  {decision === 'PASS' ? <CheckCircle2 className="h-7 w-7" /> : <ShieldAlert className="h-7 w-7" />}
                </div>
                <div>
                  <span className="text-xs font-mono uppercase tracking-wider font-bold opacity-80">
                    Release Gate Decision
                  </span>
                  <h2 className="text-2xl sm:text-3xl font-extrabold text-white tracking-tight">{decision}</h2>
                </div>
              </div>

              <span className="rounded bg-black/40 px-3 py-1 text-xs font-mono font-bold border border-white/20">
                Confidence: {Math.round((result.release_decision.data.confidence_score || 1) * 100)}%
              </span>
            </div>

            <p className="text-sm leading-relaxed opacity-90">{result.release_decision.data.summary}</p>
          </div>
        )}

        {/* AI Runtime Header — model_status is reported as-is from the backend, never
            hardcoded, so this never claims a live Gemini invocation that didn't happen.
            `result` only ever has real content once the mission completes, so while a
            mission is still running this must say so explicitly instead of falling
            through to fallback strings ("Not configured"/"n/a"/"Unknown") that read as
            a misconfiguration rather than "still in progress". */}
        <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4 flex flex-wrap items-center justify-between gap-3 text-xs">
          {isRunning ? (
            <div className="flex items-center gap-2">
              <Bot className="h-5 w-5 text-sky-400 animate-pulse" />
              <span className="font-bold text-white">AI Runtime:</span>
              <span className="text-slate-300">Pending invocation</span>
              <span className="text-slate-500">|</span>
              <span className="text-slate-400">Deterministic stages executing; Gemini not invoked yet.</span>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <Bot className="h-5 w-5 text-sky-400" />
              <span className="font-bold text-white">AI Runtime:</span>
              <span className="font-mono text-sky-300 font-bold">{result?.ai_runtime?.model_id || 'Not configured'}</span>
              <span className="text-slate-500">|</span>
              <span className="text-slate-300">Framework: {result?.ai_runtime?.framework || 'n/a'}</span>
              <span className="text-slate-500">|</span>
              <span
                className={
                  result?.ai_runtime?.model_status && !result.ai_runtime.model_status.startsWith('NOT_')
                    ? 'text-emerald-400 font-bold'
                    : 'text-slate-400 font-bold'
                }
              >
                {result?.ai_runtime?.model_status === 'NOT_INVOKED_DETERMINISTIC_PIPELINE'
                  ? 'Gemini not invoked by design — zero diffs were found deterministically, so no decision required AI judgment.'
                  : `Model Status: ${result?.ai_runtime?.model_status || 'Unknown'}`}
              </span>
            </div>
          )}
          <span className="font-mono text-[11px] text-slate-400">Strict Deterministic Boundary Enforced</span>
        </div>

        {/* Tab Navigation */}
        <div className="border-b border-slate-800">
          <nav className="flex flex-wrap gap-2 text-xs font-medium">
            {[
              { id: 'summary', label: 'Mission Summary', icon: Layers },
              { id: 'semantic', label: 'Material Findings', icon: ShieldAlert },
              { id: 'impact', label: 'Dependency DAG', icon: GitCompare },
              { id: 'experiments', label: 'Boundary Experiments', icon: Sparkles },
              { id: 'recon', label: 'Reconciliation & RCA', icon: Sliders },
              { id: 'blast', label: 'Blast Radius & Telemetry', icon: BarChart3 },
              { id: 'remediation', label: isEquivalence ? 'Alignment Options' : 'Remediation & Revalidation', icon: Check },
              { id: 'evidence', label: 'Evidence Lineage', icon: Database },
              { id: 'agent', label: 'Gemini Action Timeline', icon: Bot },
            ].map((tab) => {
              const Icon = tab.icon;
              const isActive = activeTab === tab.id;
              return (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id as typeof activeTab)}
                  className={`flex items-center gap-1.5 border-b-2 px-3.5 py-2.5 transition-colors ${
                    isActive
                      ? 'border-sky-400 text-sky-400 font-bold bg-sky-950/20'
                      : 'border-transparent text-slate-400 hover:border-slate-700 hover:text-slate-200'
                  }`}
                >
                  <Icon className="h-4 w-4" />
                  <span>{tab.label}</span>
                </button>
              );
            })}
          </nav>
        </div>

        {/* Tab Content Panels */}
        <div className="pt-2">
          {/* Tab 1: Mission Summary */}
          {activeTab === 'summary' && (
            <div className="space-y-6">
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-4 space-y-1">
                  <div className="text-xs text-slate-400">Comparison Mode</div>
                  <div className="text-lg font-extrabold text-white font-mono">{meta.mode || 'RELEASE_CONFORMANCE'}</div>
                </div>

                <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-4 space-y-1">
                  <div className="text-xs text-slate-400">Target Product & State</div>
                  <div className="text-lg font-extrabold text-sky-300 font-mono">
                    {meta.mission_object?.objective?.product || 'Unknown'}
                    {meta.mission_object?.objective?.jurisdiction ? ` (${meta.mission_object.objective.jurisdiction})` : ''}
                  </div>
                </div>

                <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-4 space-y-1">
                  <div className="text-xs text-slate-400">Effective Period</div>
                  <div className="text-lg font-extrabold text-emerald-400 font-mono">
                    {meta.mission_object?.objective?.effective_period_start || 'Unknown'}
                  </div>
                </div>
              </div>

              {/* Source Overview Card */}
              <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-5 space-y-3">
                <h3 className="text-sm font-bold text-white uppercase tracking-wider">Source Overview</h3>
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 text-xs font-mono">
                  <div className="rounded-lg border border-sky-800/80 bg-slate-950 p-3 space-y-1">
                    <div className="text-sky-400 font-bold font-sans">
                      {isEquivalence ? 'Source A:' : 'Source A (Pricing Intent):'}
                    </div>
                    <div className="text-slate-200">{meta.source_a || 'Not set'}</div>
                  </div>
                  <div className="rounded-lg border border-purple-800/80 bg-slate-950 p-3 space-y-1">
                    <div className="text-purple-400 font-bold font-sans">
                      {isEquivalence ? 'Source B:' : 'Source B (Target Rating Implementation):'}
                    </div>
                    <div className="text-slate-200">{meta.source_b || 'None (no target selected)'}</div>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Tab 2: Material Findings */}
          {activeTab === 'semantic' && (
            semanticSec?.status === 'SUCCEEDED' ? (
              <SemanticDiffViewer diffs={semanticSec.data?.differences} isCompleted={true} neutralLabels={isEquivalence} />
            ) : (
              <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-8 text-center text-xs text-slate-400 font-mono">
                Status: {semanticSec?.status || 'NOT_RUN'} — {stageStatusLine('SEMANTIC_ANALYSIS', semanticSec)}
              </div>
            )
          )}

          {/* Tab 3: Dependency DAG */}
          {activeTab === 'impact' && (
            impactSec?.status === 'SUCCEEDED' ? (
              <ImpactGraph impact={impactSec.data} isCompleted={true} />
            ) : (
              <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-8 text-center text-xs text-slate-400 font-mono">
                Status: {impactSec?.status || 'NOT_RUN'} — {stageStatusLine('IMPACT_ANALYSIS', impactSec)}
              </div>
            )
          )}

          {/* Tab 4: Boundary Experiments */}
          {activeTab === 'experiments' && (
            experimentsSec?.status === 'SUCCEEDED' ? (
              <TestPlanViewer testPlan={experimentsSec.data} isCompleted={true} neutralLabels={isEquivalence} />
            ) : (
              <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-8 text-center text-xs text-slate-400 font-mono">
                Status: {experimentsSec?.status || 'NOT_RUN'} — {stageStatusLine('RISK_DIRECTED_TESTING', experimentsSec)}
              </div>
            )
          )}

          {/* Tab 5: Reconciliation & RCA */}
          {activeTab === 'recon' && (
            reconSec?.status === 'SUCCEEDED' ? (
              <ReconciliationTrace scenario={experimentsSec?.data?.experiments?.[0] as any} isCompleted={true} neutralLabels={isEquivalence} />
            ) : (
              <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-8 text-center text-xs text-slate-400 font-mono">
                Status: {reconSec?.status || 'NOT_RUN'} — {stageStatusLine('RECONCILIATION', reconSec)}
              </div>
            )
          )}

          {/* Tab 6: Blast Radius & Telemetry */}
          {activeTab === 'blast' && (
            blastSec?.status === 'SUCCEEDED' ? (
              <PortfolioImpactFunnel portfolio={blastSec.data as any} isCompleted={true} neutralLabels={isEquivalence} />
            ) : (
              <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-8 text-center text-xs text-slate-400 font-mono">
                Status: {blastSec?.status || 'NOT_RUN'} — {stageStatusLine('PORTFOLIO_ANALYSIS', blastSec)}
              </div>
            )
          )}

          {/* Tab 7: Remediation & Revalidation / Alignment Options */}
          {activeTab === 'remediation' && (
            isEquivalence ? (
              !alignmentReference ? (
                <div className="rounded-xl border border-amber-800/60 bg-amber-950/20 p-6 space-y-4 text-center">
                  <AlertTriangle className="h-8 w-8 text-amber-400 mx-auto" />
                  <div className="text-sm font-bold text-amber-200">
                    Equivalence mode does not treat either source as authoritative
                  </div>
                  <p className="text-xs text-amber-200/80 max-w-lg mx-auto leading-relaxed">
                    Neither Source A nor Source B is presumed correct — RateGuard did not compute a directional
                    patch during this mission run. A directional alignment option has to reference one side, so
                    pick which source you want treated as the alignment reference to generate one on demand.
                  </p>
                  <div className="flex items-center justify-center gap-3">
                    <button
                      onClick={() => chooseAlignmentReference('A')}
                      className="inline-flex items-center gap-2 rounded-lg bg-sky-600 px-4 py-2 text-xs font-bold text-white hover:bg-sky-500"
                    >
                      Use Source A as reference <ArrowRight className="h-3.5 w-3.5" />
                    </button>
                    <button
                      onClick={() => chooseAlignmentReference('B')}
                      className="inline-flex items-center gap-2 rounded-lg bg-purple-600 px-4 py-2 text-xs font-bold text-white hover:bg-purple-500"
                    >
                      Use Source B as reference <ArrowRight className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              ) : alignmentLoading ? (
                <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-8 text-center text-xs text-slate-400 font-mono">
                  Generating alignment option relative to Source {alignmentReference}…
                </div>
              ) : alignmentError ? (
                <div className="rounded-xl border border-rose-800/60 bg-rose-950/20 p-6 space-y-3 text-center">
                  <AlertTriangle className="h-8 w-8 text-rose-400 mx-auto" />
                  <div className="text-sm font-bold text-rose-200">{alignmentError}</div>
                  <button
                    onClick={() => setAlignmentReference(null)}
                    className="inline-flex items-center gap-2 rounded-lg border border-slate-700 px-4 py-2 text-xs font-bold text-slate-300 hover:bg-slate-800"
                  >
                    Try again
                  </button>
                </div>
              ) : alignmentOptions ? (
                <div className="space-y-6">
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-slate-400">
                      Reference: <span className="font-bold text-white">Source {alignmentOptions.reference}</span> —{' '}
                      {alignmentOptions.difference_count} difference{alignmentOptions.difference_count === 1 ? '' : 's'} aligned
                    </span>
                    <button
                      onClick={() => { setAlignmentReference(null); setAlignmentOptions(null); }}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-[11px] font-bold text-slate-300 hover:bg-slate-800"
                    >
                      <RotateCcw className="h-3 w-3" /> Change reference
                    </button>
                  </div>

                  <div className="rounded-xl border border-sky-800/80 bg-slate-900/80 p-5 space-y-3">
                    <div className="flex items-center justify-between">
                      <h3 className="text-base font-bold text-white flex items-center gap-2">
                        <Check className="h-5 w-5 text-sky-400" /> Alignment Option (relative to Source {alignmentOptions.reference})
                      </h3>
                      <span className="font-mono text-xs text-sky-300 rounded bg-sky-950 px-2.5 py-0.5 border border-sky-800">
                        {alignmentOptions.remediation.derived_package_id}
                      </span>
                    </div>
                    <p className="text-xs text-slate-300 leading-relaxed">
                      Neither source is authoritative in Equivalence mode. Shown below is one possible alignment:
                      the changes that would make the other source compute the same premiums as Source {alignmentOptions.reference} for the scenarios tested.
                    </p>

                    {/* Changes List */}
                    <div className="space-y-2 pt-2">
                      <div className="text-xs font-bold text-slate-400 uppercase tracking-wider">Difference by scenario</div>
                      {Object.entries(alignmentOptions.remediation.proposed_changes || {}).map(([key, val]: any) => (
                        <div key={key} className="rounded-lg border border-slate-800 bg-slate-950 p-3 text-xs font-mono space-y-1">
                          <div className="font-bold text-sky-300">{val.title || key}</div>
                          <div className="grid grid-cols-2 gap-2 text-[11px]">
                            <div>Other source (current): <span className="text-rose-400 font-bold">{val.before_target_value}</span></div>
                            <div>Source {alignmentOptions.reference} (reference): <span className="text-emerald-400 font-bold">{val.proposed_intent_value}</span></div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Revalidation Results */}
                  <div className="rounded-xl border border-emerald-800/80 bg-emerald-950/20 p-5 space-y-4 shadow-xl">
                    <div className="flex items-center justify-between border-b border-emerald-800/60 pb-3">
                      <h4 className="text-sm font-bold text-emerald-300 flex items-center gap-2">
                        <TrendingDown className="h-4 w-4 text-emerald-400" /> Alignment Impact Analysis
                      </h4>
                      <span className="font-mono text-xs font-bold text-emerald-400">
                        {alignmentOptions.revalidation.exposure_eliminated_pct}% Difference Eliminated
                      </span>
                    </div>

                    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 text-xs font-mono">
                      <div className="rounded-lg border border-rose-900/60 bg-rose-950/30 p-3 space-y-1">
                        <div className="text-rose-400 font-bold font-sans">BEFORE Alignment Difference:</div>
                        <div className="text-xl font-extrabold text-rose-300">${alignmentOptions.revalidation.before_absolute_exposure}</div>
                        <div className="text-[11px] text-slate-400">{alignmentOptions.revalidation.before_affected_policies} affected policies</div>
                      </div>

                      <div className="rounded-lg border border-emerald-900/60 bg-emerald-950/30 p-3 space-y-1">
                        <div className="text-emerald-400 font-bold font-sans">AFTER Alignment Difference:</div>
                        <div className="text-xl font-extrabold text-emerald-300">${alignmentOptions.revalidation.after_absolute_exposure}</div>
                        <div className="text-[11px] text-slate-400">{alignmentOptions.revalidation.after_affected_policies} affected policies</div>
                      </div>
                    </div>
                  </div>
                </div>
              ) : null
            ) : (
              remediationSec?.status === 'SUCCEEDED' && remediationSec?.data ? (
                <div className="space-y-6">
                  <div className="rounded-xl border border-sky-800/80 bg-slate-900/80 p-5 space-y-3">
                    <div className="flex items-center justify-between">
                      <h3 className="text-base font-bold text-white flex items-center gap-2">
                        <Check className="h-5 w-5 text-sky-400" /> {remediationSec.data.title}
                      </h3>
                      <span className="font-mono text-xs text-sky-300 rounded bg-sky-950 px-2.5 py-0.5 border border-sky-800">
                        {remediationSec.data.derived_package_id}
                      </span>
                    </div>
                    <p className="text-xs text-slate-300 leading-relaxed">{remediationSec.data.rationale}</p>

                    {/* Changes List */}
                    <div className="space-y-2 pt-2">
                      <div className="text-xs font-bold text-slate-400 uppercase tracking-wider">Proposed Modifications</div>
                      {Object.entries(remediationSec.data.proposed_changes || {}).map(([key, val]: any) => (
                        <div key={key} className="rounded-lg border border-slate-800 bg-slate-950 p-3 text-xs font-mono space-y-1">
                          <div className="font-bold text-sky-300">{val.title || key}</div>
                          <div className="grid grid-cols-2 gap-2 text-[11px]">
                            <div>Target Value: <span className="text-rose-400 font-bold">{val.before_target_value}</span></div>
                            <div>Proposed Value: <span className="text-emerald-400 font-bold">{val.proposed_intent_value}</span></div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Revalidation Results */}
                  {revalidationSec?.status === 'SUCCEEDED' && revalidationSec?.data && (
                    <div className="rounded-xl border border-emerald-800/80 bg-emerald-950/20 p-5 space-y-4 shadow-xl">
                      <div className="flex items-center justify-between border-b border-emerald-800/60 pb-3">
                        <h4 className="text-sm font-bold text-emerald-300 flex items-center gap-2">
                          <TrendingDown className="h-4 w-4 text-emerald-400" /> Remediation Revalidation Analysis
                        </h4>
                        <span className="font-mono text-xs font-bold text-emerald-400">
                          {revalidationSec.data.exposure_eliminated_pct}% Exposure Eliminated
                        </span>
                      </div>

                      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 text-xs font-mono">
                        <div className="rounded-lg border border-rose-900/60 bg-rose-950/30 p-3 space-y-1">
                          <div className="text-rose-400 font-bold font-sans">BEFORE Remediation Exposure:</div>
                          <div className="text-xl font-extrabold text-rose-300">${revalidationSec.data.before_absolute_exposure}</div>
                          <div className="text-[11px] text-slate-400">{revalidationSec.data.before_affected_policies} affected policies</div>
                        </div>

                        <div className="rounded-lg border border-emerald-900/60 bg-emerald-950/30 p-3 space-y-1">
                          <div className="text-emerald-400 font-bold font-sans">AFTER Remediation Exposure:</div>
                          <div className="text-xl font-extrabold text-emerald-300">${revalidationSec.data.after_absolute_exposure}</div>
                          <div className="text-[11px] text-slate-400">{revalidationSec.data.after_affected_policies} affected policies</div>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-8 text-center text-xs text-slate-400 font-mono">
                  Status: {remediationSec?.status || 'NOT_RUN'} — {stageStatusLine('REMEDIATION', remediationSec)}
                </div>
              )
            )
          )}

          {/* Tab 8: Evidence Lineage */}
          {activeTab === 'evidence' && (
            <EvidenceLineage evidence={evidence} isCompleted={true} />
          )}

          {/* Tab 9: Agent Action Timeline */}
          {activeTab === 'agent' && (
            agentSec?.status === 'SUCCEEDED' ? (
              <AgentActivityPanel agentSteps={agentSec.data as any} isCompleted={true} />
            ) : (
              <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-8 text-center text-xs text-slate-400 font-mono">
                Status: {agentSec?.status || 'NOT_RUN'} — {stageStatusLine('DECISION', agentSec)}
              </div>
            )
          )}
        </div>
      </div>
    </MissionErrorBoundary>
  );
}
