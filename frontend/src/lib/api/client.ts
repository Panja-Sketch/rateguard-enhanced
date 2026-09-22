import {
  AssuranceMissionDetail,
  AssuranceMissionSummary,
  EvidenceRecord,
  SourceDescriptor,
  ValidationIssue,
  WorkflowEvent,
} from '../types/assurance';
import { getAuthToken, notifyUnauthorized } from '../auth/session';

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

/**
 * fetch() with the caller's Firebase ID token attached as `Authorization:
 * Bearer …` (never in the URL). On a 401 the token is force-refreshed once and
 * the request retried — a token can expire between the SDK cache check and the
 * server check. A second 401 means the session is genuinely over: the provider
 * is notified (sign-out + redirect to /login). A 403 is NOT retried and does
 * NOT sign the user out — it is a permission decision, surfaced by
 * handleResponse as a normal ApiError.
 */
export async function authFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const send = async (forceRefresh: boolean): Promise<Response> => {
    const token = await getAuthToken(forceRefresh);
    if (!token) {
      throw new ApiError('You are signed out. Please sign in again.', 401, 'AUTHENTICATION_REQUIRED');
    }
    const headers = new Headers(init.headers);
    headers.set('Authorization', `Bearer ${token}`);
    return fetch(url, { ...init, headers });
  };

  let res = await send(false);
  if (res.status === 401) {
    res = await send(true);
    if (res.status === 401) {
      notifyUnauthorized();
    }
  }
  return res;
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
    if (res.status === 401) {
      // Fixed, safe text: never echo anything the server or a token contained.
      throw new ApiError('Your session has expired. Please sign in again.', 401, code ?? 'AUTHENTICATION_REQUIRED');
    }
    if (res.status === 429) {
      const retryAfter = Number(res.headers.get('Retry-After'));
      const wait = Number.isFinite(retryAfter) && retryAfter > 0 ? ` Try again in about ${Math.ceil(retryAfter / 60) > 1 ? `${Math.ceil(retryAfter / 60)} minutes` : `${Math.ceil(retryAfter)} seconds`}.` : ' Please try again later.';
      throw new ApiError(`You have made too many requests.${wait}`, 429, code ?? 'RATE_LIMITED');
    }
    if (res.status === 403) {
      throw new ApiError('You do not have permission to perform this action.', 403, code ?? 'FORBIDDEN');
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
  const res = await authFetch(`${BASE_URL}/api/v1/missions`, {
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

  const res = await authFetch(`${BASE_URL}/api/v1/missions?${params.toString()}`, {
    cache: 'no-store',
  });
  return handleResponse(res);
}

export async function getAssuranceMission(missionId: string): Promise<AssuranceMissionDetail> {
  const res = await authFetch(`${BASE_URL}/api/v1/missions/${missionId}`, {
    cache: 'no-store',
  });
  return handleResponse<AssuranceMissionDetail>(res);
}

export async function archiveAssuranceMission(missionId: string): Promise<{
  mission_id: string;
  status: string;
  message: string;
}> {
  const res = await authFetch(`${BASE_URL}/api/v1/missions/${missionId}/archive`, {
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
  const res = await authFetch(`${BASE_URL}/api/v1/missions/${missionId}`, {
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
  const res = await authFetch(`${BASE_URL}/api/v1/missions/${missionId}/cancel`, {
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
  const res = await authFetch(`${BASE_URL}/api/v1/missions/${missionId}/retry`, {
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
  const res = await authFetch(`${BASE_URL}/api/v1/missions/${missionId}/alignment-options`, {
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
  const res = await authFetch(`${BASE_URL}/api/v1/system/info`, { cache: 'no-store' });
  return handleResponse(res);
}

export async function getAssuranceRunEvents(runId: string): Promise<{
  run_id: string;
  event_count: number;
  events: WorkflowEvent[];
}> {
  const res = await authFetch(`${BASE_URL}/api/v1/assurance/runs/${runId}/events`, {
    cache: 'no-store',
  });
  return handleResponse<{ run_id: string; event_count: number; events: WorkflowEvent[] }>(res);
}

export async function getAssuranceRunEvidence(runId: string): Promise<{
  run_id: string;
  evidence_count: number;
  evidence: EvidenceRecord[];
}> {
  const res = await authFetch(`${BASE_URL}/api/v1/assurance/runs/${runId}/evidence`, {
    cache: 'no-store',
  });
  return handleResponse<{ run_id: string; evidence_count: number; evidence: EvidenceRecord[] }>(res);
}

export async function uploadSourceFile(file: File): Promise<SourceDescriptor> {
  const formData = new FormData();
  formData.append('file', file);

  const res = await authFetch(`${BASE_URL}/api/v1/sources`, {
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

// The Controlled Workbook v1 compiler's own receipt (locked doc section 5.3):
// only present (non-null) when the compiled source was a `.xlsx` workbook.
// `status` distinguishes verified compilation from a workbook that compiled
// but has incomplete/failing control-case evidence — never conflated with a
// bare boolean pass/fail.
export interface WorkbookControlCaseResult {
  case_id: string;
  passed: boolean;
  output_id: string;
  expected: string;
  actual: string;
  difference: string;
  detail: string | null;
}

export interface WorkbookCompilationReceipt {
  artifact_sha256: string;
  compiler_version: string;
  sheets_found: string[];
  supported_constructs: string[];
  rejected_constructs: string[];
  node_counts: Record<string, number>;
  control_case_results: WorkbookControlCaseResult[];
  warnings: string[];
  errors: { code: string; message: string; details: unknown[] }[];
  metadata: Record<string, string>;
  status: 'VERIFIED' | 'REVIEW_REQUIRED' | 'REJECTED';
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
  workbook_compilation_receipt: WorkbookCompilationReceipt | null;
  ipir_package: unknown;
}> {
  const res = await authFetch(`${BASE_URL}/api/v1/sources/${sourceId}/compile`, {
    method: 'POST',
    cache: 'no-store',
  });
  return handleResponse(res);
}

// Locked doc section 13.2: safe, credential-free connector metadata only —
// never a base URL or credential. A mission may only reference a connector
// by this id + one of its allowed engine versions, never an arbitrary URL.
export interface ConnectorMetadata {
  connector_id: string;
  display_name: string;
  allowed_engine_versions: string[];
  wire_format: string;
  last_health_check_status: string | null;
}

export async function listConnectors(): Promise<ConnectorMetadata[]> {
  const res = await authFetch(`${BASE_URL}/api/v1/connectors`, { cache: 'no-store' });
  return handleResponse<ConnectorMetadata[]>(res);
}

export interface ConnectorInvocationEvidence {
  evidence_id: string;
  connector_id: string | null;
  engine_version: string | null;
  correlation_id: string | null;
  connector_request_id: string | null;
  request_sha256: string | null;
  response_sha256: string | null;
  status: string | null;
  final_premium: string | null;
  error_code: string | null;
}

export async function getConnectorEvidence(missionId: string): Promise<{
  mission_id: string;
  connector_invocation_count: number;
  connector_invocations: ConnectorInvocationEvidence[];
}> {
  const res = await authFetch(`${BASE_URL}/api/v1/missions/${missionId}/connector-evidence`, {
    cache: 'no-store',
  });
  return handleResponse(res);
}

export interface SessionInfo {
  uid: string;
  email: string | null;
  tenant_id: string;
  role: 'ADMIN' | 'RELEASE_OWNER' | 'CONSUMER_REVIEWER' | 'VIEWER';
}

/** The server's view of who the caller is. For display and navigation only —
 * every request is re-authorized on the server. */
export async function fetchSession(): Promise<SessionInfo> {
  const res = await authFetch(`${BASE_URL}/api/v1/me`, { cache: 'no-store' });
  return handleResponse<SessionInfo>(res);
}

// ---------------------------------------------------------------------------
// Connector-backed portfolio impact (Prompt 8)
// ---------------------------------------------------------------------------

export type ImpactStatus =
  | 'NOT_RUN'
  | 'QUEUED'
  | 'RUNNING'
  | 'COMPLETE'
  | 'PARTIAL'
  | 'CANCELLED'
  | 'FAILED';

export interface ImpactProgress {
  batches_total: number;
  batches_done: number;
  batches_incomplete: number;
  rows_processed: number;
  rows_in_scope: number;
  rows_total: number;
  mismatches_so_far: number;
  inconclusive_so_far: number;
  retries: number;
  elapsed_seconds: number;
}

export interface ImpactAggregate {
  status: ImpactStatus;
  impact_decision: 'BLOCK' | 'PASS_ELIGIBLE' | 'REVIEW_REQUIRED' | 'CANCELLED';
  completeness: 'COMPLETE' | 'PARTIAL' | 'CANCELLED';
  incomplete_reasons: string[];
  exposure_is_lower_bound: boolean;
  rows_total: number;
  processed_policies: number;
  eligible_policies: number;
  out_of_scope_policies: number;
  out_of_scope_reasons: Record<string, number>;
  successful_comparisons: number;
  mismatches: number;
  inconclusive: number;
  unprocessed_policies: number;
  coverage_pct: number;
  affected_pct: number;
  overcharge_count: number;
  overcharge_total: string;
  undercharge_count: number;
  undercharge_total: string;
  signed_net_delta: string;
  absolute_exposure: string;
  mean_abs_delta: string | null;
  median_abs_delta: string | null;
  min_delta: string | null;
  max_delta: string | null;
  batch_count: number;
  batches_done: number;
  batches_incomplete: number;
  retry_count: number;
  request_count: number;
  error_classes: Record<string, number>;
  budget_exhausted: string[];
  halt_reason: string | null;
  cohort_distribution: {
    minimum_cohort_size: number;
    disclaimer: string;
    cohorts: Array<{
      cohort_dimension: string;
      cohort_value: string;
      sample_size: number;
      suppressed: boolean;
      affected_rate: number | null;
      mean_absolute_change: string | null;
      overcharge_rate: number | null;
    }>;
  } | null;
  pipeline_impact: {
    as_of_date: string;
    buckets: Array<{ window_label: string; affected_renewal_count: number; total_absolute_impact: string }>;
    total_affected_renewals_next_90_days: number;
  } | null;
  provenance: Record<string, unknown>;
  result_sha256: string;
}

export interface MissionImpact {
  mission_id: string;
  status: ImpactStatus;
  reason?: string;
  job_id?: string;
  progress?: ImpactProgress;
  connector?: { connector_id: string; engine_version: string; batch_quote: boolean };
  aggregate?: ImpactAggregate | null;
}

export async function getMissionImpact(missionId: string): Promise<MissionImpact> {
  const res = await authFetch(`${BASE_URL}/api/v1/missions/${missionId}/impact`, { cache: 'no-store' });
  return handleResponse<MissionImpact>(res);
}

/** Downloads the tenant-scoped evidence bundle (ZIP) through the authenticated
 * fetch (the token is never placed in a URL) and hands it to the browser. */
export async function downloadEvidenceBundle(missionId: string): Promise<{ manifestSha256: string | null }> {
  const res = await authFetch(`${BASE_URL}/api/v1/missions/${missionId}/evidence/bundle`, { cache: 'no-store' });
  if (!res.ok) {
    await handleResponse<unknown>(res); // throws a typed ApiError
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${missionId}-evidence.zip`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  return { manifestSha256: res.headers.get('X-Manifest-SHA256') };
}
