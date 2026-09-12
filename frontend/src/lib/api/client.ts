import {
  AssuranceMissionDetail,
  AssuranceMissionSummary,
  EvidenceRecord,
  SourceDescriptor,
  ValidationIssue,
  WorkflowEvent,
} from '../types/assurance';

const BASE_URL =
  process.env.NEXT_PUBLIC_RATEGUARD_API_URL || 'http://localhost:8000';

export class ApiError extends Error {
  status: number;
  code?: string;
  issues?: ValidationIssue[];

  constructor(message: string, status: number, code?: string, issues?: ValidationIssue[]) {
    super(message);
    this.status = status;
    this.name = 'ApiError';
    this.code = code;
    this.issues = issues;
  }
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let errorDetail = `HTTP ${res.status}: ${res.statusText}`;
    let code: string | undefined;
    let issues: ValidationIssue[] | undefined;
    try {
      const data = await res.json();
      if (data.detail) {
        if (typeof data.detail === 'string') {
          errorDetail = data.detail;
        } else {
          errorDetail = data.detail.message || JSON.stringify(data.detail);
          code = data.detail.code;
          if (Array.isArray(data.detail.issues)) {
            issues = data.detail.issues as ValidationIssue[];
          }
        }
      } else if (data.message) {
        errorDetail = data.message;
      }
    } catch {
      // Ignore non-JSON body errors
    }
    throw new ApiError(errorDetail, res.status, code, issues);
  }
  return res.json() as Promise<T>;
}

export async function fetchHealth(): Promise<{ status: string; service: string }> {
  const res = await fetch(`${BASE_URL}/health`, { cache: 'no-store' });
  return handleResponse<{ status: string; service: string }>(res);
}

/**
 * True when `err` is the browser's own transport-level failure (DNS
 * failure, connection refused, or a CORS-blocked response) rather than a
 * structured API error. `fetch()` rejects with a bare `TypeError` in this
 * case — never an ApiError, since no HTTP response was ever received to
 * parse a body from. Distinguishing this is what lets callers show
 * "RateGuard API is currently unreachable" instead of the browser's raw
 * "Failed to fetch" message.
 */
export function isNetworkUnreachableError(err: unknown): boolean {
  return err instanceof TypeError;
}

/** Renders `err` as user-facing text, replacing a raw transport failure
 * with an actionable, non-technical message instead of the browser's
 * "Failed to fetch". `context` names what didn't happen (e.g. "No mission
 * was created.") so the message tells the user exactly what state they're
 * left in. */
export function describeFetchError(err: unknown, context: string): string {
  if (isNetworkUnreachableError(err)) {
    return `RateGuard API is currently unreachable. ${context}`;
  }
  return err instanceof Error ? err.message : String(err);
}

export async function createAssuranceMission(params: {
  name: string;
  mode: string;
  product: string;
  jurisdiction: string;
  effective_period_start: string;
  portfolio_dataset: string;
  gating_policy: string;
  source_a: Record<string, unknown> | null;
  source_b?: Record<string, unknown> | null;
  disposable_sample_run?: boolean;
  is_demo_sample?: boolean;
}): Promise<{
  mission_id: string;
  status: string;
  mode: string;
  decision: string;
  result: Record<string, unknown>;
}> {
  const res = await fetch(`${BASE_URL}/api/v1/missions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
    cache: 'no-store',
  });
  return handleResponse(res);
}

export async function listAssuranceMissions(
  limit: number = 50,
  offset: number = 0,
  filters?: { status?: string; mode?: string; decision?: string; includeDemoSamples?: boolean }
): Promise<{
  missions: AssuranceMissionSummary[];
  total_count: number;
  limit: number;
  offset: number;
}> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (filters?.status) params.append('status', filters.status);
  if (filters?.mode) params.append('mode', filters.mode);
  if (filters?.decision) params.append('decision', filters.decision);
  if (filters?.includeDemoSamples) params.append('include_demo_samples', 'true');

  const res = await fetch(`${BASE_URL}/api/v1/missions?${params.toString()}`, {
    cache: 'no-store',
  });
  return handleResponse(res);
}

export async function getAssuranceMission(missionId: string): Promise<AssuranceMissionDetail> {
  const res = await fetch(`${BASE_URL}/api/v1/missions/${missionId}`, {
    cache: 'no-store',
  });
  return handleResponse<AssuranceMissionDetail>(res);
}

export async function archiveAssuranceMission(missionId: string): Promise<{
  mission_id: string;
  status: string;
  message: string;
}> {
  const res = await fetch(`${BASE_URL}/api/v1/missions/${missionId}/archive`, {
    method: 'POST',
    cache: 'no-store',
  });
  return handleResponse(res);
}

export async function deleteAssuranceMission(missionId: string): Promise<{
  mission_id: string;
  status: string;
  message: string;
}> {
  const res = await fetch(`${BASE_URL}/api/v1/missions/${missionId}`, {
    method: 'DELETE',
    cache: 'no-store',
  });
  return handleResponse(res);
}

export async function cancelAssuranceMission(missionId: string): Promise<{
  mission_id: string;
  status: string;
  cancellation_requested?: boolean;
  message: string;
}> {
  const res = await fetch(`${BASE_URL}/api/v1/missions/${missionId}/cancel`, {
    method: 'POST',
    cache: 'no-store',
  });
  return handleResponse(res);
}

export async function retryAssuranceMission(missionId: string): Promise<{
  mission_id: string;
  status: string;
  attempt_number: number;
  message: string;
}> {
  const res = await fetch(`${BASE_URL}/api/v1/missions/${missionId}/retry`, {
    method: 'POST',
    cache: 'no-store',
  });
  return handleResponse(res);
}

export interface AlignmentOptionsResult {
  mission_id: string;
  reference: 'A' | 'B';
  difference_count: number;
  remediation: {
    remediation_id: string;
    title: string;
    rationale: string;
    derived_package_id: string;
    proposed_changes: Record<string, any>;
    source_evidence_ref: string;
  };
  revalidation: {
    revalidation_id: string;
    remediation_id: string;
    before_absolute_exposure: string;
    after_absolute_exposure: string;
    before_affected_policies: number;
    after_affected_policies: number;
    exposure_eliminated_pct: number;
  };
}

// Equivalence mode never generates a directional patch during the mission
// run itself (neither Source A nor Source B is presumed authoritative) --
// this computes one on demand, only after the caller has explicitly picked
// which source to treat as the alignment reference.
export async function generateAlignmentOptions(
  missionId: string,
  reference: 'A' | 'B'
): Promise<AlignmentOptionsResult> {
  const res = await fetch(`${BASE_URL}/api/v1/missions/${missionId}/alignment-options`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reference }),
    cache: 'no-store',
  });
  return handleResponse(res);
}

export async function fetchSystemInfo(): Promise<{
  gemini_model: string;
  gemini_model_display: string;
  agent_framework: string;
  agent_provider: string;
  agent_supervisor: string;
  ipir_version: string;
  cloud_project: string;
}> {
  const res = await fetch(`${BASE_URL}/api/v1/system/info`, { cache: 'no-store' });
  return handleResponse(res);
}

export async function getAssuranceRunEvents(runId: string): Promise<{
  run_id: string;
  event_count: number;
  events: WorkflowEvent[];
}> {
  const res = await fetch(`${BASE_URL}/api/v1/assurance/runs/${runId}/events`, {
    cache: 'no-store',
  });
  return handleResponse<{ run_id: string; event_count: number; events: WorkflowEvent[] }>(res);
}

export async function getAssuranceRunEvidence(runId: string): Promise<{
  run_id: string;
  evidence_count: number;
  evidence: EvidenceRecord[];
}> {
  const res = await fetch(`${BASE_URL}/api/v1/assurance/runs/${runId}/evidence`, {
    cache: 'no-store',
  });
  return handleResponse<{ run_id: string; evidence_count: number; evidence: EvidenceRecord[] }>(res);
}

export async function uploadSourceFile(file: File): Promise<SourceDescriptor> {
  const formData = new FormData();
  formData.append('file', file);

  const res = await fetch(`${BASE_URL}/api/v1/sources`, {
    method: 'POST',
    body: formData,
    cache: 'no-store',
  });

  return handleResponse<SourceDescriptor>(res);
}

export interface CompilationReceipt {
  product: string;
  product_line: string;
  jurisdiction: string;
  effective_period_start: string;
  effective_period_end: string | null;
  input_count: number;
  constant_count: number;
  table_count: number;
  table_row_count: number;
  rule_count: number;
  calculation_count: number;
  output_count: number;
  output_node_ids: string[];
}

export async function compileSource(sourceId: string): Promise<{
  source_id: string;
  adapter_id: string;
  ipir_package_id: string;
  mapping_coverage: number;
  confidence: number;
  warnings: string[];
  requires_human_review: boolean;
  compilation_receipt: CompilationReceipt;
  ipir_package: unknown;
}> {
  const res = await fetch(`${BASE_URL}/api/v1/sources/${sourceId}/compile`, {
    method: 'POST',
    cache: 'no-store',
  });
  return handleResponse(res);
}
