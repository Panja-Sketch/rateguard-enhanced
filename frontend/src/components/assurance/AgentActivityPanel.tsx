'use client';

import { WorkflowEvent } from '@/lib/types/assurance';
import { Bot, Terminal, Activity, Clock, Sparkles, AlertTriangle, UserCheck } from 'lucide-react';

export interface AgentStep {
  step_index?: number;
  agent_name?: string;
  agent?: string;
  agent_role?: string;
  role?: string;
  action?: string;
  action_type?: string;
  summary?: string;
  rationale?: string;
  selected_tool?: string;
  type?: string;
  output_snapshot?: Record<string, unknown>;
  timestamp?: string;
  model_id?: string | null;
  invocation_id?: string | null;
  decision_type?: string | null;
  is_gemini_decision?: boolean;
  is_fallback?: boolean;
  fallback_reason?: string | null;
  needs_human_review?: boolean;
}

interface AgentActivityPanelProps {
  summary?: string;
  events?: WorkflowEvent[];
  agentSteps?: AgentStep[];
  isCompleted?: boolean;
}

export function AgentActivityPanel({
  summary,
  events = [],
  agentSteps = [],
  isCompleted = true,
}: AgentActivityPanelProps) {
  // Combine agent steps and workflow events into a chronological list. Type
  // classification is driven by the actual persisted evidence fields
  // (is_gemini_decision / is_fallback), never inferred from action-name
  // heuristics — so this timeline can never claim a Gemini call happened when
  // it didn't.
  const combinedItems = [
    ...agentSteps.map((s, idx) => {
      const type = s.is_gemini_decision
        ? 'GEMINI_DECISION'
        : s.is_fallback
        ? 'DETERMINISTIC_FALLBACK'
        : (s.type || (s.action_type === 'TOOL_INVOCATION' || s.selected_tool ? 'TOOL' : 'REASONING')).toUpperCase();
      return {
        id: `step-${s.step_index ?? idx}`,
        title: s.agent_role || s.agent_name || s.agent || 'AssuranceSupervisor',
        role: s.role || (s.decision_type ? `Decision: ${s.decision_type}` : 'Deterministic Stage'),
        description: s.summary || '',
        rationale: s.rationale,
        modelId: s.is_gemini_decision ? s.model_id : null,
        fallbackReason: s.fallback_reason,
        needsHumanReview: s.needs_human_review,
        type,
        timestamp: s.timestamp,
      };
    }),
    ...events.map((e, idx) => ({
      id: `evt-${e.event_id || idx}`,
      title: e.stage || 'WORKFLOW_MILESTONE',
      role: 'Orchestrator Stage',
      description: e.message || '',
      rationale: undefined as string | undefined,
      modelId: null as string | null,
      fallbackReason: undefined as string | undefined,
      needsHumanReview: false,
      type: 'WORKFLOW',
      timestamp: e.timestamp,
    })),
  ];

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4 sm:p-6 shadow-xl space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-base font-semibold text-white flex items-center gap-2">
            <Bot className="h-5 w-5 text-sky-400" />
            Gemini Decisions & Deterministic Tool Invocations
          </h3>
          <p className="text-xs text-slate-400 mt-0.5">
            Only real, persisted events — a Gemini decision is shown only when an actual invocation occurred
          </p>
        </div>
        <span className="rounded bg-sky-950 px-2.5 py-1 text-xs font-mono font-medium text-sky-300 border border-sky-800">
          gemini-3.7-flash via google-genai
        </span>
      </div>

      {combinedItems.length === 0 ? (
        <div className="rounded-xl border border-slate-800 bg-slate-950 p-8 text-center text-xs text-slate-400">
          <Activity className="h-6 w-6 text-sky-400 animate-pulse mx-auto mb-2" />
          {isCompleted
            ? 'Workflow complete.'
            : 'Waiting for agent reasoning and tool invocations...'}
        </div>
      ) : (
        <div className="space-y-2.5">
          {combinedItems.map((item) => (
            <div
              key={item.id}
              className="flex items-start gap-3 rounded-lg border border-slate-800 bg-slate-950 p-3.5 text-xs transition-all hover:border-slate-700"
            >
              {item.type === 'GEMINI_DECISION' ? (
                <div className="mt-0.5 rounded bg-sky-950 p-1.5 text-sky-400 border border-sky-800">
                  <Sparkles className="h-4 w-4" />
                </div>
              ) : item.type === 'DETERMINISTIC_FALLBACK' ? (
                <div className="mt-0.5 rounded bg-amber-950 p-1.5 text-amber-400 border border-amber-800">
                  <AlertTriangle className="h-4 w-4" />
                </div>
              ) : item.type === 'TOOL' ? (
                <div className="mt-0.5 rounded bg-purple-950 p-1.5 text-purple-400 border border-purple-800">
                  <Terminal className="h-4 w-4" />
                </div>
              ) : item.type === 'REASONING' ? (
                <div className="mt-0.5 rounded bg-slate-800 p-1.5 text-slate-300 border border-slate-700">
                  <Bot className="h-4 w-4" />
                </div>
              ) : (
                <div className="mt-0.5 rounded bg-slate-800 p-1.5 text-slate-300 border border-slate-700">
                  <Activity className="h-4 w-4" />
                </div>
              )}

              <div className="flex-1 space-y-1">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="font-bold text-white">{item.title}</span>
                    <span className="text-[11px] text-slate-400 font-sans">({item.role})</span>
                    {item.needsHumanReview && (
                      <span className="flex items-center gap-1 rounded bg-rose-950 px-1.5 py-0.5 text-[10px] font-mono font-semibold text-rose-300 border border-rose-800">
                        <UserCheck className="h-3 w-3" /> HUMAN REVIEW
                      </span>
                    )}
                  </div>
                  <span
                    className={`rounded px-2 py-0.5 text-[10px] font-mono font-semibold border ${
                      item.type === 'GEMINI_DECISION'
                        ? 'bg-sky-950 text-sky-300 border-sky-800'
                        : item.type === 'DETERMINISTIC_FALLBACK'
                        ? 'bg-amber-950 text-amber-300 border-amber-800'
                        : item.type === 'TOOL'
                        ? 'bg-purple-950 text-purple-300 border-purple-800'
                        : item.type === 'REASONING'
                        ? 'bg-slate-800 text-slate-300 border-slate-700'
                        : 'bg-slate-800 text-slate-300 border-slate-700'
                    }`}
                  >
                    {item.type === 'GEMINI_DECISION'
                      ? `GEMINI DECISION${item.modelId ? ` · ${item.modelId}` : ''}`
                      : item.type === 'DETERMINISTIC_FALLBACK'
                      ? 'DETERMINISTIC FALLBACK'
                      : item.type === 'TOOL'
                      ? 'DETERMINISTIC TOOL'
                      : item.type === 'REASONING'
                      ? 'DETERMINISTIC STAGE'
                      : 'WORKFLOW EVENT'}
                  </span>
                </div>

                <p className="text-slate-200 text-xs leading-relaxed">{item.description}</p>

                {item.rationale && (
                  <p className="text-slate-400 text-[11px] leading-relaxed italic">&ldquo;{item.rationale}&rdquo;</p>
                )}

                {item.fallbackReason && (
                  <p className="text-amber-400 text-[11px] font-mono">Fallback reason: {item.fallbackReason}</p>
                )}

                {item.timestamp && (
                  <div className="flex items-center gap-1 text-[10px] font-mono text-slate-500">
                    <Clock className="h-3 w-3" />
                    <span>{new Date(item.timestamp).toLocaleTimeString()}</span>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
