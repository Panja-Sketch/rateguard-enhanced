export type MissionStatus =
  | 'DRAFT'
  | 'VALIDATING'
  | 'QUEUED'
  | 'RUNNING'
  | 'WAITING_RETRY'
  | 'NEEDS_REVIEW'
  | 'COMPLETED'
  | 'FAILED'
  | 'CANCELLED'
  | 'ARCHIVED';

export type ComparisonMode = 'EQUIVALENCE' | 'RELEASE_CONFORMANCE';
export type AnalysisStatus = 'NOT_AVAILABLE' | 'NOT_RUN' | 'RUNNING' | 'SUCCEEDED' | 'FAILED';

export interface ValidationIssue {
  field: string;
  code: string;
  message: string;
  severity?: 'ERROR' | 'WARNING' | 'INFO';
}

export interface PricingSourceRef {
  source_id: string;
  source_type: 'FILE' | 'REGISTERED_ID' | 'API_CONNECTOR' | 'SAMPLE_RELEASE';
  name: string;
  format?: string;
  hash_checksum?: string;
  compiled_package_id?: string;
}

export interface AgentAction {
  action_id: string;
  agent_role: string;
  action_type: 'REASONING' | 'TOOL_INVOCATION' | 'EXPERIMENT' | 'DECISION';
  summary: string;
  rationale?: string;
  selected_tool?: string;
  latency_ms: number;
  // Only set when this specific action is backed by a real, completed Gemini
  // invocation — never a hardcoded/default claim.
  model_id?: string | null;
  invocation_id?: string | null;
  decision_type?: string | null;
  is_gemini_decision?: boolean;
  is_fallback?: boolean;
  fallback_reason?: string | null;
  needs_human_review?: boolean;
  timestamp: string;
}

export interface MaterialFinding {
  finding_id: string;
  category: string;
  severity: 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW';
  title: string;
  description: string;
  intent_value?: string;
  target_value?: string;
  affected_node?: string;
}

export interface SemanticDiffItem {
  // A real mission's MaterialFinding payload only ever sends finding_id,
  // category, severity, description, intent_value/target_value, and
  // affected_node -- id/difference_type/semantic_path/left_value/right_value
  // are legacy raw-SemanticDifference field names that no live API response
  // actually populates, so they're optional here rather than falsely
  // guaranteed.
  id?: string;
  finding_id?: string;
  category?: string;
  severity: 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW';
  difference_type?: string;
  semantic_path?: string;
  left_value?: string;
  right_value?: string;
  description: string;
  provenance?: string;
  affected_output?: string;
  intent_value?: string;
  target_value?: string;
}

export interface RuntimeExperiment {
  experiment_id: string;
  scenario_id?: string;
  name?: string;
  probe_name: string;
  category: string;
  risk_inputs: Record<string, unknown>;
  expected_premium: string;
  actual_premium: string;
  matches: boolean;
  trace_diverged?: boolean;
  first_divergent_node?: string | null;
  first_divergence_node?: string | null;
}

export interface TestScenario extends RuntimeExperiment {
  scenario_id: string;
  name: string;
  trace_diverged: boolean;
  description?: string;
}

export interface TestPlan {
  total_candidates_generated: number;
  total_scenarios_selected: number;
  selected_scenarios: TestScenario[];
  coverage_summary?: Record<string, string | number>;
  total_generated?: number;
  total_executed?: number;
  match_count?: number;
  mismatch_count?: number;
  reduction_pct?: number;
  experiments?: TestScenario[];
}

export interface PredicateClause {
  field: string;
  operator: string;
  value: string | number | boolean;
}

export interface ImpactPredicate {
  id: string;
  description: string;
  clauses: PredicateClause[];
  logical_operator?: string;
}

export interface ImpactAnalysis {
  impacted_calculation_nodes: string[];
  affected_pricing_outputs: string[];
  candidate_risk_predicates: ImpactPredicate[];
  summary: string;
  changed_nodes?: string[];
  risk_predicates?: Record<string, unknown>[];
}

export interface RootCauseFinding {
  node_id: string;
  title: string;
  explanation: string;
  expected_value: string;
  actual_value: string;
  divergence_type: string;
}

export interface BlastRadiusResult {
  total_policies_analyzed: number;
  semantically_exposed_count: number;
  behaviorally_affected_count: number;
  financially_affected_count: number;
  undercharged_policy_count: number;
  overcharged_policy_count: number;
  total_undercharge_amount: string;
  total_overcharge_amount: string;
  signed_net_variance: string;
  absolute_financial_exposure: string;
  multi_defect_policy_count: number;
  portfolio_execution_seconds: number;
  measured_throughput_policies_per_sec: number;
  financially_affected_pct?: number;
}

export interface PortfolioExposureResult extends BlastRadiusResult {
  portfolio_id: string;
  total_policies: number;
  exposed_policy_count: number;
  exposed_policy_pct: number;
  total_signed_variance: string;
  total_absolute_variance: string;
}

export interface RemediationProposal {
  remediation_id: string;
  title: string;
  rationale: string;
  derived_package_id: string;
  proposed_changes: Record<string, unknown>;
  source_evidence_ref: string;
}

export interface RevalidationResult {
  revalidation_id: string;
  remediation_id: string;
  before_absolute_exposure: string;
  after_absolute_exposure: string;
  before_affected_policies: number;
  after_affected_policies: number;
  exposure_eliminated_pct: number;
  new_release_decision: string;
  targeted_tests_rerun?: number;
  targeted_tests_passed?: number;
  regression_tests_rerun?: number;
  regression_tests_passed?: number;
}

export interface ReleaseDecision {
  status: 'PASS' | 'REVIEW_REQUIRED' | 'BLOCK_DEPLOYMENT';
  confidence_score: number;
  summary: string;
  blocking_reasons: string[];
  recommendation: string;
}

export interface SectionResult<T> {
  status: AnalysisStatus;
  reason?: string | null;
  error_message?: string | null;
  data?: T | null;
}

export interface AssuranceResultV2 {
  mission_id: string;
  mode: ComparisonMode;
  overall_status: string;
  ai_runtime: {
    model_id: string;
    framework: string;
    model_status: string;
  };
  validation: SectionResult<ValidationIssue[]>;
  agent_execution: SectionResult<AgentAction[]>;
  semantic_analysis: SectionResult<{
    difference_count: number;
    differences: SemanticDiffItem[];
    summary: string;
  }>;
  impact_analysis: SectionResult<{
    changed_nodes: string[];
    impacted_calculation_nodes: string[];
    affected_pricing_outputs: string[];
    risk_predicates: Record<string, unknown>[];
  }>;
  experiments: SectionResult<{
    total_generated: number;
    total_executed: number;
    match_count: number;
    mismatch_count: number;
    reduction_pct: number;
    experiments: TestScenario[];
  }>;
  reconciliation: SectionResult<{
    mismatch_count: number;
    first_divergent_node?: string | null;
    root_cause?: RootCauseFinding | null;
  }>;
  blast_radius: SectionResult<BlastRadiusResult>;
  remediation: SectionResult<RemediationProposal>;
  revalidation: SectionResult<RevalidationResult>;
  release_decision: SectionResult<ReleaseDecision>;
  evidence_refs: string[];
  telemetry: Record<string, unknown>;
}

export interface EligibleActions {
  cancel: boolean;
  retry: boolean;
  archive: boolean;
  delete: boolean;
}

export interface AssuranceMissionSummary {
  mission_id: string;
  name: string;
  created_at: string;
  updated_at?: string;
  status: MissionStatus | string;
  status_reason?: string | null;
  current_stage?: string | null;
  workflow_stage?: string;
  attempt_number?: number;
  mode: ComparisonMode | string;
  source_a: string | null;
  source_b?: string | null;
  decision: string;
  summary: string;
  disposable_sample_run: boolean;
  is_demo_sample?: boolean;
  eligible_actions: EligibleActions;
}

export interface AssuranceMissionDetail {
  mission_id: string;
  status: MissionStatus | string;
  status_reason?: string | null;
  current_stage?: string | null;
  workflow_stage: string;
  attempt_number?: number;
  cancellation_requested?: boolean;
  queued_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  decision?: string | null;
  summary?: string | null;
  updated_at: string;
  metadata?: Record<string, unknown>;
  is_demo_sample?: boolean;
  eligible_actions: EligibleActions;
  result: Record<string, unknown>;
}

export interface SourceDescriptor {
  id: string;
  source_id: string;
  name: string;
  type: string;
  format: string;
  package_id?: string;
}

export interface EvidenceRecord {
  evidence_id: string;
  run_id: string;
  evidence_type: string;
  title: string;
  description: string;
  source_ref?: string;
  target_ref?: string;
  data_summary?: Record<string, unknown>;
  created_at?: string;
  metadata?: Record<string, unknown>;
  summary?: string;
  timestamp?: string;
}

export interface WorkflowEvent {
  event_id: string;
  stage: string;
  message: string;
  timestamp: string;
}

