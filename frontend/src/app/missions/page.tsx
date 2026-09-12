'use client';

import { useEffect, useState, useCallback } from 'react';
import Link from 'next/link';
import {
  listAssuranceMissions,
  archiveAssuranceMission,
  deleteAssuranceMission,
  cancelAssuranceMission,
  retryAssuranceMission,
  describeFetchError,
  ApiError,
} from '@/lib/api/client';
import { AssuranceMissionSummary, ComparisonMode } from '@/lib/types/assurance';
import {
  ListFilter,
  RefreshCw,
  Plus,
  Eye,
  Archive,
  Trash2,
  ShieldAlert,
  CheckCircle2,
  AlertTriangle,
  Clock,
  Layers,
  ShieldCheck,
  X,
  Ban,
  RotateCcw,
  Sparkles,
} from 'lucide-react';

export default function MissionsHistoryPage() {
  const [missions, setMissions] = useState<AssuranceMissionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filters & Pagination
  const [modeFilter, setModeFilter] = useState<string>('ALL');
  const [statusFilter, setStatusFilter] = useState<string>('ALL');
  const [decisionFilter, setDecisionFilter] = useState<string>('ALL');
  const [showDemoSamples, setShowDemoSamples] = useState(false);
  const [page, setPage] = useState(0);
  const limit = 20;

  // Deletion Safeguard Modal State
  const [deleteModalMission, setDeleteModalMission] = useState<AssuranceMissionSummary | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  // Cancel Safeguard Modal State
  const [cancelModalMission, setCancelModalMission] = useState<AssuranceMissionSummary | null>(null);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);

  // Per-row action errors (archive/retry) that must not hide the mission row.
  const [actionErrors, setActionErrors] = useState<Record<string, string>>({});

  const fetchMissions = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listAssuranceMissions(limit, page * limit, {
        mode: modeFilter === 'ALL' ? undefined : modeFilter,
        status: statusFilter === 'ALL' ? undefined : statusFilter,
        decision: decisionFilter === 'ALL' ? undefined : decisionFilter,
        includeDemoSamples: showDemoSamples,
      });
      // Only ever replaces the list on a SUCCESSFUL load — a failed request
      // (see catch below) leaves whatever was last successfully loaded in
      // place, rather than fabricating an empty history.
      setMissions(res.missions || []);
    } catch (err: unknown) {
      setError(describeFetchError(err, 'Mission history could not be loaded.'));
    } finally {
      setLoading(false);
    }
  }, [page, limit, modeFilter, statusFilter, decisionFilter, showDemoSamples]);

  useEffect(() => {
    fetchMissions();
  }, [fetchMissions]);

  const handleArchive = async (missionId: string) => {
    setActionErrors((prev) => ({ ...prev, [missionId]: '' }));
    try {
      await archiveAssuranceMission(missionId);
      fetchMissions();
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err);
      setActionErrors((prev) => ({ ...prev, [missionId]: msg }));
    }
  };

  const handleRetry = async (missionId: string) => {
    setActionErrors((prev) => ({ ...prev, [missionId]: '' }));
    try {
      await retryAssuranceMission(missionId);
      fetchMissions();
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err);
      setActionErrors((prev) => ({ ...prev, [missionId]: msg }));
    }
  };

  const confirmCancel = async () => {
    if (!cancelModalMission) return;
    setCancelling(true);
    setCancelError(null);
    try {
      await cancelAssuranceMission(cancelModalMission.mission_id);
      setCancelModalMission(null);
      fetchMissions();
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        setCancelError(err.message);
      } else {
        setCancelError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setCancelling(false);
    }
  };

  const confirmDelete = async () => {
    if (!deleteModalMission) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteAssuranceMission(deleteModalMission.mission_id);
      setDeleteModalMission(null);
      fetchMissions();
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        setDeleteError(err.message);
      } else {
        setDeleteError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="space-y-8 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 border-b border-slate-800 pb-4">
        <div>
          <h1 className="text-2xl sm:text-3xl font-extrabold text-white">Assurance Mission History</h1>
          <p className="text-xs sm:text-sm text-slate-400 mt-1">
            Audit log of pricing release assurance missions, verification traces, and release gating decisions.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={fetchMissions}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-800 bg-slate-900 px-3.5 py-2 text-xs font-semibold text-slate-300 hover:bg-slate-850"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} /> Refresh
          </button>
          <Link
            href="/missions/new"
            className="inline-flex items-center gap-2 rounded-xl bg-sky-600 px-4 py-2 text-xs font-bold text-white hover:bg-sky-500 transition-all shadow-lg"
          >
            <Plus className="h-4 w-4" /> Start Mission
          </Link>
        </div>
      </div>

      {error && (
        <div className="flex items-center justify-between gap-4 rounded-xl border border-rose-800 bg-rose-950/50 p-4 text-xs text-rose-300 font-mono">
          <span>[Error] {error}</span>
          <button
            onClick={fetchMissions}
            disabled={loading}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-rose-700 bg-rose-900/60 px-3 py-1.5 font-sans font-semibold text-rose-200 hover:bg-rose-900 disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} /> Retry
          </button>
        </div>
      )}

      {/* Filter Bar */}
      <div className="flex flex-wrap items-center gap-3 rounded-xl border border-slate-800 bg-slate-900/60 p-4">
        <div className="flex items-center gap-2 text-xs text-slate-400 font-bold">
          <ListFilter className="h-4 w-4 text-sky-400" /> Filters:
        </div>

        {/* Mode Filter */}
        <select
          value={modeFilter}
          onChange={(e) => {
            setModeFilter(e.target.value);
            setPage(0);
          }}
          className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-1.5 text-xs text-slate-200 focus:border-sky-500 focus:outline-none"
        >
          <option value="ALL">All Modes</option>
          <option value="RELEASE_CONFORMANCE">Release Conformance</option>
          <option value="EQUIVALENCE">Equivalence</option>
        </select>

        {/* Status Filter */}
        <select
          value={statusFilter}
          onChange={(e) => {
            setStatusFilter(e.target.value);
            setPage(0);
          }}
          className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-1.5 text-xs text-slate-200 focus:border-sky-500 focus:outline-none"
        >
          <option value="ALL">All Statuses</option>
          <option value="COMPLETED">Completed</option>
          <option value="RUNNING">Running</option>
          <option value="FAILED">Failed</option>
          <option value="ARCHIVED">Archived</option>
        </select>

        {/* Decision Filter */}
        <select
          value={decisionFilter}
          onChange={(e) => {
            setDecisionFilter(e.target.value);
            setPage(0);
          }}
          className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-1.5 text-xs text-slate-200 focus:border-sky-500 focus:outline-none"
        >
          <option value="ALL">All Decisions</option>
          <option value="PASS">PASS</option>
          <option value="BLOCK_DEPLOYMENT">BLOCK_DEPLOYMENT</option>
          <option value="REVIEW_REQUIRED">REVIEW_REQUIRED</option>
        </select>

        {/* Demo/dev sample visibility toggle — hidden by default so failed
            development/demo missions don't clutter real mission history. */}
        <label className="flex items-center gap-1.5 text-xs text-slate-400 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={showDemoSamples}
            onChange={(e) => {
              setShowDemoSamples(e.target.checked);
              setPage(0);
            }}
          />
          Show demo/dev samples
        </label>
      </div>

      {/* Missions Table */}
      <div className="overflow-x-auto rounded-xl border border-slate-800 bg-slate-900/80 shadow-xl">
        <table className="w-full text-left text-xs font-sans">
          <thead className="border-b border-slate-800 bg-slate-950 text-slate-400">
            <tr>
              <th className="px-4 py-3 font-medium">Mission ID</th>
              <th className="px-4 py-3 font-medium">Mode & Scope</th>
              <th className="px-4 py-3 font-medium">Status / Stage</th>
              <th className="px-4 py-3 font-medium">Release Decision</th>
              <th className="px-4 py-3 font-medium text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/60 font-mono">
            {loading && missions.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-slate-400 font-sans">
                  Loading mission history…
                </td>
              </tr>
            ) : missions.length === 0 && error ? (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-slate-400 font-sans">
                  Mission history is unavailable right now — see the error above. This is not necessarily an empty history.
                </td>
              </tr>
            ) : missions.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-slate-400 font-sans">
                  No assurance missions found matching criteria.
                </td>
              </tr>
            ) : (
              missions.map((m) => {
                const actions = m.eligible_actions || { cancel: false, retry: false, archive: false, delete: false };
                const isDemo = m.is_demo_sample ?? m.disposable_sample_run;
                const rowError = actionErrors[m.mission_id];
                return (
                <tr key={m.mission_id} className="transition-colors hover:bg-slate-800/40">
                  <td className="px-4 py-3 font-bold text-sky-400">{m.mission_id}</td>
                  <td className="px-4 py-3 font-sans">
                    <div className="font-bold text-white flex items-center gap-1.5">
                      {m.name}
                      {isDemo && (
                        <span
                          className="inline-flex items-center gap-1 rounded bg-amber-950 px-1.5 py-0.5 text-[10px] font-bold text-amber-300 border border-amber-800"
                          title="Uses a bundled RateGuard demo/sample source or endpoint"
                        >
                          <Sparkles className="h-2.5 w-2.5" /> Demo sample
                        </span>
                      )}
                    </div>
                    <div className="text-[11px] text-slate-400 flex items-center gap-1.5 mt-0.5">
                      <span className="rounded bg-sky-950 px-1.5 py-0.5 text-[10px] text-sky-300 font-mono border border-sky-800">
                        {m.mode}
                      </span>
                      <span>{m.source_a || 'no source A'} {m.source_b ? `↔ ${m.source_b}` : ''}</span>
                    </div>
                    {rowError && (
                      <div className="mt-1 rounded border border-rose-800 bg-rose-950/50 px-2 py-1 text-[10px] text-rose-300 font-mono">
                        {rowError}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <span className="rounded px-2 py-0.5 text-[10px] font-bold bg-slate-800 text-slate-300 border border-slate-700">
                      {m.status}
                    </span>
                    {m.status_reason && (
                      <div className="text-[10px] text-slate-500 mt-1 max-w-[16rem]">{m.status_reason}</div>
                    )}
                  </td>
                  <td className="px-4 py-3 font-sans">
                    <span
                      className={`rounded px-2.5 py-1 text-xs font-bold border ${
                        m.decision === 'PASS'
                          ? 'bg-emerald-950 text-emerald-300 border-emerald-800'
                          : m.decision === 'BLOCK_DEPLOYMENT'
                          ? 'bg-rose-950 text-rose-300 border-rose-800'
                          : 'bg-amber-950 text-amber-300 border-amber-800'
                      }`}
                    >
                      {m.decision}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right font-sans">
                    <div className="flex items-center justify-end gap-2">
                      <Link
                        href={`/missions/${m.mission_id}`}
                        className="inline-flex items-center gap-1 rounded bg-sky-950 px-2.5 py-1 text-xs font-bold text-sky-300 border border-sky-800 hover:bg-sky-900 transition-colors"
                      >
                        <Eye className="h-3.5 w-3.5" /> View
                      </Link>

                      {actions.cancel && (
                        <button
                          onClick={() => setCancelModalMission(m)}
                          className="inline-flex items-center gap-1 rounded bg-amber-950 px-2.5 py-1 text-xs font-medium text-amber-300 border border-amber-800 hover:bg-amber-900 transition-colors"
                          title="Cancel mission"
                        >
                          <Ban className="h-3.5 w-3.5" />
                        </button>
                      )}

                      {actions.retry && (
                        <button
                          onClick={() => handleRetry(m.mission_id)}
                          className="inline-flex items-center gap-1 rounded bg-slate-800 px-2.5 py-1 text-xs font-medium text-slate-300 hover:bg-slate-700 transition-colors"
                          title="Retry failed mission"
                        >
                          <RotateCcw className="h-3.5 w-3.5" />
                        </button>
                      )}

                      {actions.archive && (
                        <button
                          onClick={() => handleArchive(m.mission_id)}
                          className="inline-flex items-center gap-1 rounded bg-slate-800 px-2.5 py-1 text-xs font-medium text-slate-300 hover:bg-slate-700 transition-colors"
                          title="Archive audit record"
                        >
                          <Archive className="h-3.5 w-3.5" />
                        </button>
                      )}

                      {actions.delete && (
                        <button
                          onClick={() => setDeleteModalMission(m)}
                          className="inline-flex items-center gap-1 rounded bg-rose-950 px-2.5 py-1 text-xs font-medium text-rose-300 border border-rose-800 hover:bg-rose-900 transition-colors"
                          title="Delete disposable run"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* Cancel Confirmation Modal */}
      {cancelModalMission && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4">
          <div className="w-full max-w-md rounded-2xl border border-amber-800 bg-slate-900 p-6 space-y-4 shadow-2xl">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <div className="flex items-center gap-2 text-amber-400 font-bold text-base">
                <Ban className="h-5 w-5" /> Cancel Mission
              </div>
              <button
                onClick={() => {
                  setCancelModalMission(null);
                  setCancelError(null);
                }}
                className="text-slate-400 hover:text-white"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="space-y-2 text-xs text-slate-300">
              <p>
                You requested cancellation of mission <strong className="text-white font-mono">{cancelModalMission.mission_id}</strong>.
              </p>
              <div className="rounded-lg border border-slate-800 bg-slate-950 p-3 text-[11px] font-mono text-slate-400 space-y-1">
                <div>Current status: <span className="text-white">{cancelModalMission.status}</span></div>
              </div>
              <p className="text-amber-300 leading-relaxed font-sans">
                {cancelModalMission.status === 'RUNNING'
                  ? 'This mission is actively running — cancellation will be requested and honored cooperatively at the next stage checkpoint, not instantly.'
                  : 'This mission will transition directly to CANCELLED and stop being processed.'}
              </p>
            </div>

            {cancelError && (
              <div className="rounded-lg border border-rose-800 bg-rose-950 p-3 text-xs text-rose-300 font-mono">
                [Cancel Failed] {cancelError}
              </div>
            )}

            <div className="flex items-center justify-end gap-3 pt-2 border-t border-slate-800">
              <button
                onClick={() => {
                  setCancelModalMission(null);
                  setCancelError(null);
                }}
                className="rounded-lg border border-slate-800 bg-slate-900 px-4 py-2 text-xs font-semibold text-slate-300 hover:bg-slate-800"
              >
                Dismiss
              </button>
              <button
                onClick={confirmCancel}
                disabled={cancelling}
                className="rounded-lg bg-amber-600 px-4 py-2 text-xs font-bold text-white hover:bg-amber-500 transition-all disabled:opacity-50"
              >
                {cancelling ? 'Cancelling...' : 'Confirm Cancel'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete Safeguard Modal */}
      {deleteModalMission && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4">
          <div className="w-full max-w-md rounded-2xl border border-rose-800 bg-slate-900 p-6 space-y-4 shadow-2xl">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <div className="flex items-center gap-2 text-rose-400 font-bold text-base">
                <AlertTriangle className="h-5 w-5" /> Mission Deletion Safeguard
              </div>
              <button
                onClick={() => {
                  setDeleteModalMission(null);
                  setDeleteError(null);
                }}
                className="text-slate-400 hover:text-white"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="space-y-2 text-xs text-slate-300">
              <p>
                You requested permanent deletion of mission <strong className="text-white font-mono">{deleteModalMission.mission_id}</strong>.
              </p>
              <div className="rounded-lg border border-slate-800 bg-slate-950 p-3 text-[11px] font-mono text-slate-400 space-y-1">
                <div>Mode: <span className="text-sky-300">{deleteModalMission.mode}</span></div>
                <div>Status: <span className="text-white">{deleteModalMission.status}</span></div>
                <div>Decision: <span className="text-amber-300">{deleteModalMission.decision}</span></div>
              </div>
              <p className="text-rose-300 leading-relaxed font-sans">
                Completed audit records are protected against permanent deletion to preserve compliance provenance. Disposable sample runs and drafts may be deleted.
              </p>
            </div>

            {deleteError && (
              <div className="rounded-lg border border-rose-800 bg-rose-950 p-3 text-xs text-rose-300 font-mono">
                [Deletion Blocked] {deleteError}
              </div>
            )}

            <div className="flex items-center justify-end gap-3 pt-2 border-t border-slate-800">
              <button
                onClick={() => {
                  setDeleteModalMission(null);
                  setDeleteError(null);
                }}
                className="rounded-lg border border-slate-800 bg-slate-900 px-4 py-2 text-xs font-semibold text-slate-300 hover:bg-slate-800"
              >
                Cancel
              </button>
              <button
                onClick={confirmDelete}
                disabled={deleting}
                className="rounded-lg bg-rose-600 px-4 py-2 text-xs font-bold text-white hover:bg-rose-500 transition-all disabled:opacity-50"
              >
                {deleting ? 'Deleting...' : 'Confirm Permanent Delete'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

