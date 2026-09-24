'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  ApiError,
  uploadSourceFile,
  compileSource,
  createAssuranceMission,
  describeFetchError,
  CompilationReceipt,
  WorkbookCompilationReceipt,
  ConnectorMetadata,
  listConnectors,
} from '@/lib/api/client';
import { SourceDescriptor, ValidationIssue } from '@/lib/types/assurance';
import { CANDIDATE_VERIFY_NAME_FORMAT, MISSION_NAME_MAX_LENGTH, normalizeMissionName } from '@/lib/missionName';
import {
  FileCode2,
  Upload,
  CheckCircle2,
  AlertCircle,
  Play,
  ArrowRight,
  Sparkles,
  Download,
  Info,
  Database,
} from 'lucide-react';

const DEMO_LEFT_PACKAGE_ID = 'AZ_HO3_2026_09';
const DEMO_RIGHT_PACKAGE_ID = 'AZ_HO3_2026_09_DEFECTIVE';

interface Compiled {
  ipir_package_id: string;
  adapter_id: string;
  confidence: number;
  warnings: string[];
  requires_human_review: boolean;
  compilation_receipt: CompilationReceipt;
  workbook_compilation_receipt: WorkbookCompilationReceipt | null;
}

export default function SourcesPage() {
  const router = useRouter();
  const [fileA, setFileA] = useState<File | null>(null);
  const [fileB, setFileB] = useState<File | null>(null);

  const [sourceA, setSourceA] = useState<SourceDescriptor | null>(null);
  const [sourceB, setSourceB] = useState<SourceDescriptor | null>(null);

  const [compiledA, setCompiledA] = useState<Compiled | null>(null);
  const [compiledB, setCompiledB] = useState<Compiled | null>(null);

  const [uploadingA, setUploadingA] = useState(false);
  const [uploadingB, setUploadingB] = useState(false);
  const [running, setRunning] = useState(false);
  const [missionName, setMissionName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [fieldIssuesA, setFieldIssuesA] = useState<ValidationIssue[]>([]);
  const [fieldIssuesB, setFieldIssuesB] = useState<ValidationIssue[]>([]);

  // Explicit opt-in only — real uploaded sources are never silently replaced with
  // the bundled Arizona demo packages.
  const [useDemoSample, setUseDemoSample] = useState(false);

  // Source B may instead be a registered REST connector — a dropdown
  // selection only, never a free-text URL (locked doc section 8.2/13.2:
  // "Users cannot provide a URL per mission").
  const [useConnectorForB, setUseConnectorForB] = useState(false);
  const [connectors, setConnectors] = useState<ConnectorMetadata[]>([]);
  const [connectorId, setConnectorId] = useState<string>('');
  const [engineVersion, setEngineVersion] = useState<string>('');
  const [connectorsError, setConnectorsError] = useState<string | null>(null);

  useEffect(() => {
    listConnectors()
      .then((list) => {
        setConnectors(list);
        if (list.length > 0) {
          setConnectorId(list[0].connector_id);
          setEngineVersion(list[0].allowed_engine_versions[0] || '');
        }
      })
      .catch((err) => setConnectorsError(describeFetchError(err, 'Registered connectors could not be loaded.')));
  }, []);

  const selectedConnector = connectors.find((c) => c.connector_id === connectorId) || null;

  const hasRealSourceA = !!compiledA?.ipir_package_id;
  const hasRealSourceB = useConnectorForB ? !!(connectorId && engineVersion) : !!compiledB?.ipir_package_id;
  const hasRealSources = hasRealSourceA && hasRealSourceB;
  const canExecute = hasRealSources || useDemoSample;

  const metadataMismatch =
    hasRealSources &&
    !useConnectorForB &&
    compiledA &&
    compiledB &&
    (compiledA.compilation_receipt.product_line !== compiledB.compilation_receipt.product_line ||
      compiledA.compilation_receipt.jurisdiction !== compiledB.compilation_receipt.jurisdiction);

  const handleUploadA = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!fileA) return;
    setUploadingA(true);
    setError(null);
    setFieldIssuesA([]);
    try {
      const desc = await uploadSourceFile(fileA);
      setSourceA(desc);
      const compiled = await compileSource(desc.source_id);
      setCompiledA(compiled);
    } catch (err: unknown) {
      if (err instanceof ApiError && err.issues && err.issues.length > 0) {
        setFieldIssuesA(err.issues);
      } else {
        setError(describeFetchError(err, 'Source A could not be compiled.'));
      }
    } finally {
      setUploadingA(false);
    }
  };

  const handleUploadB = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!fileB) return;
    setUploadingB(true);
    setError(null);
    setFieldIssuesB([]);
    try {
      const desc = await uploadSourceFile(fileB);
      setSourceB(desc);
      const compiled = await compileSource(desc.source_id);
      setCompiledB(compiled);
    } catch (err: unknown) {
      if (err instanceof ApiError && err.issues && err.issues.length > 0) {
        setFieldIssuesB(err.issues);
      } else {
        setError(describeFetchError(err, 'Source B could not be compiled.'));
      }
    } finally {
      setUploadingB(false);
    }
  };

  const handleLaunchFromSources = async () => {
    if (!canExecute) {
      setError('Upload and compile both Source A and Source B before executing, or explicitly enable the demo sample.');
      return;
    }
    // Optional display name; blank keeps the legacy default. The API re-validates.
    const nameResult = normalizeMissionName(missionName);
    if (!nameResult.ok) {
      setError(nameResult.error);
      return;
    }
    setRunning(true);
    setError(null);
    try {
      const sourceARef = hasRealSources
        ? {
            source_id: sourceA!.source_id,
            source_type: 'FILE',
            name: sourceA!.name,
            compiled_package_id: compiledA!.ipir_package_id,
            requires_human_review: compiledA!.requires_human_review,
          }
        : {
            source_id: DEMO_LEFT_PACKAGE_ID,
            source_type: 'SAMPLE_RELEASE',
            name: 'Arizona HO3 Actuarial Spec (Canonical Filing Intent)',
          };
      const sourceBRef = useConnectorForB && hasRealSourceB
        ? {
            source_id: connectorId,
            source_type: 'API_CONNECTOR',
            name: `${selectedConnector?.display_name || connectorId} (${engineVersion})`,
            connector_id: connectorId,
            engine_version: engineVersion,
          }
        : hasRealSources
        ? {
            source_id: sourceB!.source_id,
            source_type: 'FILE',
            name: sourceB!.name,
            compiled_package_id: compiledB!.ipir_package_id,
            requires_human_review: compiledB!.requires_human_review,
          }
        : {
            source_id: DEMO_RIGHT_PACKAGE_ID,
            source_type: 'SAMPLE_RELEASE',
            name: 'Arizona HO3 Target Rating Engine Implementation',
          };

      // The mission's objective (product/jurisdiction/effective date) is
      // always derived from what was actually compiled, never a fixed
      // Arizona/HO3 default — an uploaded Nevada auto source must show up
      // as Nevada auto, not silently redisplay as Arizona Homeowners.
      const receipt = hasRealSources ? compiledA!.compilation_receipt : null;

      const res = await createAssuranceMission({
        name: nameResult.value,
        mode: 'RELEASE_CONFORMANCE',
        product: receipt?.product || 'AZ_HO3',
        jurisdiction: receipt?.jurisdiction || 'Arizona',
        effective_period_start: receipt?.effective_period_start || '2026-09-01',
        portfolio_dataset: 'az_ho3_2026_synthetic_50k.csv',
        gating_policy: 'STRICT_ZERO_DRIFT',
        source_a: sourceARef,
        source_b: sourceBRef,
        disposable_sample_run: true,
        is_demo_sample: !hasRealSources,
      });
      if (res.mission_id) {
        router.push(`/missions/${res.mission_id}`);
      }
    } catch (err: unknown) {
      setError(describeFetchError(err, 'No mission was created.'));
      setRunning(false);
    }
  };

  const renderIssues = (issues: ValidationIssue[]) => (
    <div className="rounded-lg border border-rose-800 bg-rose-950/50 p-3 text-xs text-rose-300 space-y-1 font-mono">
      <div className="font-bold font-sans">Schema validation failed:</div>
      {issues.map((issue, idx) => (
        <div key={idx}>
          <span className="text-rose-400 font-bold">{issue.field}</span>
          <span className="text-rose-500"> [{issue.code}]</span>: {issue.message}
        </div>
      ))}
    </div>
  );

  const renderReceipt = (compiled: Compiled) => {
    const r = compiled.compilation_receipt;
    return (
      <div className="rounded-lg border border-emerald-800/80 bg-emerald-950/30 p-3 text-xs space-y-2 font-mono">
        <div className="flex items-center gap-1.5 text-emerald-400 font-bold font-sans">
          <CheckCircle2 className="h-4 w-4" /> Compilation Receipt — what RateGuard actually parsed
        </div>
        <div className="text-slate-300">Package ID: <span className="text-white">{compiled.ipir_package_id}</span></div>
        <div className="text-slate-300">Product: <span className="text-white">{r.product}</span> (<span className="text-sky-300">{r.product_line}</span>)</div>
        <div className="text-slate-300">Jurisdiction: <span className="text-white">{r.jurisdiction}</span></div>
        <div className="text-slate-300">
          Effective period: <span className="text-white">{r.effective_period_start}</span>
          {r.effective_period_end ? <> – <span className="text-white">{r.effective_period_end}</span></> : ' (open-ended)'}
        </div>
        <div className="text-slate-300 grid grid-cols-2 gap-x-4 gap-y-0.5">
          <span>Inputs: <span className="text-white">{r.input_count}</span></span>
          <span>Constants: <span className="text-white">{r.constant_count}</span></span>
          <span>Tables: <span className="text-white">{r.table_count}</span> ({r.table_row_count} rows)</span>
          <span>Rules: <span className="text-white">{r.rule_count}</span></span>
          <span>Calculations: <span className="text-white">{r.calculation_count}</span></span>
          <span>Outputs: <span className="text-white">{r.output_count}</span></span>
        </div>
        {r.output_node_ids.length > 0 && (
          <div className="text-slate-300">Output nodes: <span className="text-sky-300">{r.output_node_ids.join(', ')}</span></div>
        )}
        <div className="text-slate-300">Adapter: <span className="text-sky-300">{compiled.adapter_id}</span> · Confidence: <span className="text-emerald-300 font-bold">{Math.round((compiled.confidence || 1) * 100)}%</span></div>
        {compiled.requires_human_review && (
          <div className="flex items-center gap-1.5 text-amber-300 font-sans font-bold">
            <AlertCircle className="h-3.5 w-3.5" /> Low-confidence extraction — flagged for human review, not silently trusted.
          </div>
        )}
        {compiled.warnings.length > 0 && (
          <div className="text-amber-300 font-sans">
            Warnings: {compiled.warnings.join('; ')}
          </div>
        )}
      </div>
    );
  };

  const renderWorkbookReceipt = (receipt: WorkbookCompilationReceipt) => {
    const statusColor =
      receipt.status === 'VERIFIED'
        ? 'text-emerald-400 border-emerald-800 bg-emerald-950/30'
        : receipt.status === 'REVIEW_REQUIRED'
        ? 'text-amber-300 border-amber-800 bg-amber-950/30'
        : 'text-rose-300 border-rose-800 bg-rose-950/30';
    return (
      <div className={`rounded-lg border p-3 text-xs space-y-2 font-mono ${statusColor}`}>
        <div className="font-bold font-sans">Workbook Compilation Receipt — status: {receipt.status}</div>
        <div className="text-slate-300">Compiler version: <span className="text-white">{receipt.compiler_version}</span></div>
        <div className="text-slate-300 break-all">SHA-256: <span className="text-white">{receipt.artifact_sha256}</span></div>
        {receipt.control_case_results.length > 0 && (
          <div className="text-slate-300 space-y-0.5">
            <div>Control cases:</div>
            {receipt.control_case_results.map((c) => (
              <div key={c.case_id} className={c.passed ? 'text-emerald-300' : 'text-rose-300'}>
                {c.case_id}: expected {c.expected}, actual {c.actual} — {c.passed ? 'PASS' : 'FAIL'}
              </div>
            ))}
          </div>
        )}
        {receipt.errors.length > 0 && (
          <div className="text-rose-300 space-y-0.5">
            {receipt.errors.map((e, i) => (
              <div key={i}>[{e.code}] {e.message}</div>
            ))}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="space-y-8 max-w-5xl mx-auto">
      <div>
        <h1 className="text-2xl sm:text-3xl font-extrabold text-white flex items-center gap-2">
          <FileCode2 className="h-7 w-7 text-sky-400" /> Source Ingestion
        </h1>
        <p className="text-xs sm:text-sm text-slate-400 mt-1">
          Upload a RateGuard-supported source template; the compiler validates the schema and fails closed
          when required pricing elements cannot be verified. RateGuard does not claim to analyze an arbitrary
          spreadsheet or filing PDF — only what it can genuinely and verifiably compile.
        </p>
      </div>

      {error && (
        <div className="rounded-xl border border-rose-800 bg-rose-950/50 p-4 text-xs text-rose-300 font-mono">
          [Error] {error}
        </div>
      )}

      {/* Supported Format */}
      <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-5 space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 font-bold text-white text-sm">
            <FileCode2 className="h-4 w-4 text-sky-400" />
            Native IPIR / Structured JSON
          </div>
          <span className="font-mono text-[10px] text-sky-300 rounded bg-sky-950 px-2 py-0.5 border border-sky-800">
            .json
          </span>
        </div>
        <p className="text-xs text-slate-400 leading-relaxed">
          The canonical IPIR 0.1 schema, parsed directly from your uploaded JSON — every table, rule, and
          output in the file you upload is what RateGuard actually compiles. RateGuard does not have a built,
          tested adapter for any specific rating-platform export format (Guidewire, Duck Creek, or otherwise)
          today; a platform&apos;s pricing logic can be verified once it is expressed as IPIR JSON, or once the
          platform is reachable through the vendor-neutral REST connector contract described on the{' '}
          <a href="/architecture" className="text-sky-300 underline hover:text-sky-200">architecture page</a>.
        </p>
        <div className="flex flex-wrap items-center gap-3 pt-1">
          <a
            href="/samples/rateguard-source-template-a.json"
            download
            className="inline-flex items-center gap-1.5 rounded-lg border border-sky-800 bg-sky-950/40 px-3 py-1.5 text-xs font-bold text-sky-300 hover:bg-sky-950"
          >
            <Download className="h-3.5 w-3.5" /> Download sample template
          </a>
          <a
            href="/samples/rateguard-source-template-b-drift.json"
            download
            className="inline-flex items-center gap-1.5 rounded-lg border border-purple-800 bg-purple-950/40 px-3 py-1.5 text-xs font-bold text-purple-300 hover:bg-purple-950"
          >
            <Download className="h-3.5 w-3.5" /> Download one-factor drift pair
          </a>
        </div>
        <p className="text-xs text-slate-400 leading-relaxed">
          Upload Template A as Source A and Template B as Source B. Expected: one roof-age factor difference
          and a <span className="text-rose-300 font-bold">BLOCK_DEPLOYMENT</span> decision. Upload Template A as
          both Source A and Source B instead to see a clean <span className="text-emerald-300 font-bold">PASS</span> with zero differences.
        </p>
        <div className="rounded-lg border border-slate-800 bg-slate-950 p-3 text-xs text-slate-400 space-y-1.5">
          <div className="flex items-center gap-1.5 font-bold text-slate-300 font-sans">
            <Info className="h-3.5 w-3.5 text-sky-400" /> What this file must contain
          </div>
          <p className="leading-relaxed">
            Required top-level fields: <code className="text-sky-300">id</code>, <code className="text-sky-300">name</code>,{' '}
            <code className="text-sky-300">product</code> (with <code className="text-sky-300">line</code> and{' '}
            <code className="text-sky-300">jurisdiction</code>), <code className="text-sky-300">effective_period</code>,{' '}
            <code className="text-sky-300">inputs</code>, <code className="text-sky-300">constants</code>,{' '}
            <code className="text-sky-300">tables</code>, <code className="text-sky-300">calculations</code>, and{' '}
            <code className="text-sky-300">outputs</code>. Unknown or misnamed top-level fields (e.g. a friendly{' '}
            <code className="text-rose-300">rating_tables</code> instead of <code className="text-sky-300">tables</code>) are
            rejected with a schema error, never silently dropped. See the full schema and a worked example in the{' '}
            <a href="https://github.com/Panja-Sketch/rateguard-ai#supported-source-format-json-schema" target="_blank" rel="noreferrer" className="underline text-sky-300">README</a>.
          </p>
        </div>
      </div>

      {/* Supported Format: Controlled Workbook v1 */}
      <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-5 space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 font-bold text-white text-sm">
            <FileCode2 className="h-4 w-4 text-emerald-400" />
            RateGuard Controlled Workbook v1
          </div>
          <span className="font-mono text-[10px] text-emerald-300 rounded bg-emerald-950 px-2 py-0.5 border border-emerald-800">
            .xlsx
          </span>
        </div>
        <p className="text-xs text-slate-400 leading-relaxed">
          A constrained, documented spreadsheet contract (fixed <code className="text-emerald-300">RG_*</code> sheet
          names/columns, a safe calculation mini-DSL, embedded golden control cases) — never arbitrary Excel. Macro-
          enabled files, external links, OLE objects, password-protected workbooks, and unsupported formulas are
          rejected with the exact sheet/cell/function location, not silently ignored.
        </p>
        <div className="flex flex-wrap items-center gap-3 pt-1">
          <a
            href="/samples/rateguard-workbook-sample.xlsx"
            download
            className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-800 bg-emerald-950/40 px-3 py-1.5 text-xs font-bold text-emerald-300 hover:bg-emerald-950"
          >
            <Download className="h-3.5 w-3.5" /> Download sample workbook
          </a>
          <a
            href="/samples/rateguard-workbook-sample-b-drift.xlsx"
            download
            className="inline-flex items-center gap-1.5 rounded-lg border border-purple-800 bg-purple-950/40 px-3 py-1.5 text-xs font-bold text-purple-300 hover:bg-purple-950"
          >
            <Download className="h-3.5 w-3.5" /> Download one-factor drift pair
          </a>
        </div>
        <p className="text-xs text-slate-400 leading-relaxed">
          Upload the sample workbook as Source A and the drift-pair workbook as Source B to see the same
          roof-age-factor drift demonstrated in the JSON template pair, driven entirely from <code>.xlsx</code>.
        </p>
        <div className="rounded-lg border border-slate-800 bg-slate-950 p-3 text-xs text-slate-400 space-y-1.5">
          <div className="flex items-center gap-1.5 font-bold text-slate-300 font-sans">
            <Info className="h-3.5 w-3.5 text-emerald-400" /> What this file must contain
          </div>
          <p className="leading-relaxed">
            Seven required sheets, each with fixed column headers: <code className="text-emerald-300">RG_METADATA</code>{' '}
            (key/value: <code className="text-emerald-300">package_id</code>, <code className="text-emerald-300">product_id</code>,{' '}
            <code className="text-emerald-300">line</code>, <code className="text-emerald-300">country</code>,{' '}
            <code className="text-emerald-300">currency</code>, <code className="text-emerald-300">effective_start</code>),{' '}
            <code className="text-emerald-300">RG_INPUTS</code>, <code className="text-emerald-300">RG_CONSTANTS</code>,{' '}
            <code className="text-emerald-300">RG_TABLES</code> (range or exact-match lookup rows),{' '}
            <code className="text-emerald-300">RG_CALCULATIONS</code> (mini-DSL operators:{' '}
            <code className="text-emerald-300">ADD SUBTRACT MULTIPLY DIVIDE MIN MAX ROUND LOOKUP IF</code>, never a
            live Excel formula), <code className="text-emerald-300">RG_OUTPUTS</code> (each must resolve to a{' '}
            <code className="text-emerald-300">ROUND</code> calculation node), and{' '}
            <code className="text-emerald-300">RG_CONTROL_CASES</code> (golden input/output examples — at least one
            passing case is required for a <code className="text-emerald-300">VERIFIED</code> compilation, not just a
            structurally valid one). Only <code className="text-emerald-300">USD</code>/<code className="text-emerald-300">US-AZ</code> are
            in scope for this deployment today. See the full contract and worked example in the{' '}
            <a href="https://github.com/Panja-Sketch/rateguard-ai#supported-source-format-controlled-workbook-v1-xlsx" target="_blank" rel="noreferrer" className="underline text-emerald-300">README</a>.
          </p>
        </div>
        <div className="rounded-lg border border-rose-900/60 bg-rose-950/20 p-3 text-xs text-rose-200 flex items-start gap-2">
          <AlertCircle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
          <span>
            <span className="font-bold">Not supported:</span> legacy <code>.xls</code>, PDF, macros/VBA, arbitrary
            Excel formulas, or any workbook outside the documented <code>RG_*</code> contract.
          </span>
        </div>
      </div>

      {/* Dual Source Upload & Compilation Panels */}
      <div className="grid grid-cols-1 gap-6 sm:grid-cols-2">
        {/* Source A: Pricing Intent */}
        <div className="rounded-2xl border border-sky-800/60 bg-slate-900/80 p-5 space-y-4 shadow-xl">
          <div className="flex items-center justify-between">
            <span className="rounded bg-sky-950 px-2.5 py-0.5 text-xs font-bold text-sky-300 border border-sky-800">
              Source A
            </span>
            <span className="text-xs text-slate-400 font-mono">Spec / Filing</span>
          </div>

          <form onSubmit={handleUploadA} className="space-y-3">
            <input
              type="file"
              accept=".json,application/json,.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              onChange={(e) => setFileA(e.target.files?.[0] || null)}
              className="block w-full text-xs text-slate-400 file:mr-3 file:py-1.5 file:px-3 file:rounded file:border-0 file:text-xs file:font-semibold file:bg-sky-950 file:text-sky-300 border border-slate-800 rounded-lg p-2 bg-slate-950"
            />
            <button
              type="submit"
              disabled={!fileA || uploadingA}
              className="w-full inline-flex items-center justify-center gap-1.5 rounded-lg bg-sky-600 px-4 py-2 text-xs font-bold text-white hover:bg-sky-500 transition-all disabled:opacity-50"
            >
              <Upload className="h-3.5 w-3.5" />
              {uploadingA ? 'Compiling to IPIR...' : 'Upload & Compile Source A'}
            </button>
          </form>

          {fieldIssuesA.length > 0 && renderIssues(fieldIssuesA)}
          {compiledA && renderReceipt(compiledA)}
          {compiledA?.workbook_compilation_receipt && renderWorkbookReceipt(compiledA.workbook_compilation_receipt)}
        </div>

        {/* Source B: Target Engine Implementation, or a live connector */}
        <div className="rounded-2xl border border-purple-800/60 bg-slate-900/80 p-5 space-y-4 shadow-xl">
          <div className="flex items-center justify-between">
            <span className="rounded bg-purple-950 px-2.5 py-0.5 text-xs font-bold text-purple-300 border border-purple-800">
              Source B
            </span>
            <span className="text-xs text-slate-400 font-mono">Implementation / Candidate</span>
          </div>

          <div className="flex rounded-lg border border-slate-800 overflow-hidden text-xs font-bold">
            <button
              type="button"
              onClick={() => setUseConnectorForB(false)}
              className={`flex-1 px-3 py-1.5 ${!useConnectorForB ? 'bg-purple-950 text-purple-300' : 'bg-slate-950 text-slate-500'}`}
            >
              Upload File
            </button>
            <button
              type="button"
              onClick={() => setUseConnectorForB(true)}
              className={`flex-1 px-3 py-1.5 ${useConnectorForB ? 'bg-purple-950 text-purple-300' : 'bg-slate-950 text-slate-500'}`}
            >
              Live Connector
            </button>
          </div>

          {!useConnectorForB ? (
            <>
              <form onSubmit={handleUploadB} className="space-y-3">
                <input
                  type="file"
                  accept=".json,application/json,.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                  onChange={(e) => setFileB(e.target.files?.[0] || null)}
                  className="block w-full text-xs text-slate-400 file:mr-3 file:py-1.5 file:px-3 file:rounded file:border-0 file:text-xs file:font-semibold file:bg-purple-950 file:text-purple-300 border border-slate-800 rounded-lg p-2 bg-slate-950"
                />
                <button
                  type="submit"
                  disabled={!fileB || uploadingB}
                  className="w-full inline-flex items-center justify-center gap-1.5 rounded-lg bg-purple-600 px-4 py-2 text-xs font-bold text-white hover:bg-purple-500 transition-all disabled:opacity-50"
                >
                  <Upload className="h-3.5 w-3.5" />
                  {uploadingB ? 'Compiling to IPIR...' : 'Upload & Compile Source B'}
                </button>
              </form>

              {fieldIssuesB.length > 0 && renderIssues(fieldIssuesB)}
              {compiledB && renderReceipt(compiledB)}
              {compiledB?.workbook_compilation_receipt && renderWorkbookReceipt(compiledB.workbook_compilation_receipt)}
            </>
          ) : (
            <div className="space-y-3">
              <p className="text-xs text-slate-400 leading-relaxed">
                Select a registered connector and engine version. RateGuard never accepts an arbitrary URL for a
                mission — only a connector an administrator has already registered.
              </p>
              {connectorsError && (
                <div className="rounded-lg border border-rose-800 bg-rose-950/50 p-2 text-xs text-rose-300">{connectorsError}</div>
              )}
              <label className="block text-[11px] font-bold text-slate-400">Connector</label>
              <select
                value={connectorId}
                onChange={(e) => {
                  const next = connectors.find((c) => c.connector_id === e.target.value);
                  setConnectorId(e.target.value);
                  setEngineVersion(next?.allowed_engine_versions[0] || '');
                }}
                className="w-full rounded-lg border border-slate-800 bg-slate-950 p-2 text-xs text-white"
              >
                {connectors.length === 0 && <option value="">No connectors registered</option>}
                {connectors.map((c) => (
                  <option key={c.connector_id} value={c.connector_id}>
                    {c.display_name} ({c.wire_format})
                  </option>
                ))}
              </select>
              {selectedConnector && (
                <p className="text-[11px] text-slate-500">
                  Wire contract: <span className="font-mono text-slate-400">{selectedConnector.wire_format}</span>
                  {selectedConnector.wire_format === 'vendor_gateway_v1' &&
                    ' — a nested, camelCase request/response envelope, not RateGuard’s own contract, proving the connector client adapts to a genuinely different vendor shape.'}
                </p>
              )}
              <label className="block text-[11px] font-bold text-slate-400">Engine version</label>
              <select
                value={engineVersion}
                onChange={(e) => setEngineVersion(e.target.value)}
                className="w-full rounded-lg border border-slate-800 bg-slate-950 p-2 text-xs text-white"
              >
                {(selectedConnector?.allowed_engine_versions || []).map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>
      </div>

      {metadataMismatch && (
        <div className="rounded-xl border border-amber-800/60 bg-amber-950/20 p-4 text-xs text-amber-200 flex items-start gap-2.5">
          <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
          <span>
            <span className="font-bold">Metadata inconsistency:</span> Source A and Source B compiled to different
            product lines or jurisdictions ({compiledA?.compilation_receipt.product_line} / {compiledA?.compilation_receipt.jurisdiction}
            {' '}vs {compiledB?.compilation_receipt.product_line} / {compiledB?.compilation_receipt.jurisdiction}). The mission
            will still run, but the release decision cannot PASS on a mismatch like this — RateGuard&apos;s supervisor blocks it
            server-side and returns REVIEW_REQUIRED with the reason attached, since comparing two different products is not a
            meaningful equivalence check.
          </span>
        </div>
      )}

      {/* Launch Assurance from Sources Banner */}
      <div className="rounded-2xl border border-slate-800 bg-slate-900/90 p-6 space-y-4 shadow-xl">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <h3 className="text-base font-bold text-white flex items-center gap-2">
              <Play className="h-4 w-4 text-sky-400" /> Run Assurance on Ingested Sources
            </h3>
            <p className="text-xs text-slate-400 mt-0.5">
              Launch agentic assurance workflow directly comparing compiled Source A against Source B.
            </p>
            <div className="mt-3 space-y-1">
              <label htmlFor="mission-name" className="text-xs font-bold text-slate-300">
                Mission name <span className="font-normal text-slate-500">(optional)</span>
              </label>
              <input
                id="mission-name"
                data-testid="mission-name-input"
                type="text"
                value={missionName}
                maxLength={MISSION_NAME_MAX_LENGTH}
                onChange={(e) => setMissionName(e.target.value)}
                placeholder="Assurance Mission Launched from Sources"
                autoComplete="off"
                spellCheck={false}
                className="w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-xs text-white focus:border-sky-500 focus:outline-none"
              />
              <p className="text-[11px] text-slate-500">
                Display label only. For a candidate verification mission paste{' '}
                <span className="font-mono text-slate-400 break-all">{CANDIDATE_VERIFY_NAME_FORMAT}</span>.
              </p>
            </div>
          </div>

          <button
            onClick={handleLaunchFromSources}
            disabled={running || !canExecute}
            title={!canExecute ? 'Upload and compile both sources, or enable the demo sample below.' : undefined}
            className="inline-flex items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-sky-600 to-purple-600 px-6 py-3 text-xs font-bold text-white hover:opacity-90 transition-all shadow-lg disabled:opacity-50 shrink-0"
          >
            {running ? 'Launching Workflow...' : 'Execute Assurance (A ↔ B)'}
            <ArrowRight className="h-4 w-4" />
          </button>
        </div>

        {!hasRealSources && (
          <label className="flex items-start gap-2.5 rounded-lg border border-amber-800/60 bg-amber-950/20 p-3 text-xs text-amber-200 cursor-pointer">
            <input
              type="checkbox"
              checked={useDemoSample}
              onChange={(e) => setUseDemoSample(e.target.checked)}
              className="mt-0.5"
            />
            <span>
              <span className="font-bold flex items-center gap-1.5">
                <Sparkles className="h-3.5 w-3.5" /> Use demo sample
              </span>
              <span className="block text-amber-200/80 mt-0.5">
                No files uploaded and compiled yet. Check this to explicitly run against the bundled Arizona HO3
                canonical vs. defective demo packages instead — this is never selected automatically.
              </span>
            </span>
          </label>
        )}
        {!hasRealSources && useDemoSample && (
          <div className="rounded-lg border border-sky-800/60 bg-sky-950/20 px-3 py-2 text-[11px] font-mono text-sky-300 flex items-center gap-1.5">
            <Database className="h-3.5 w-3.5" /> Demo sample selected: {DEMO_LEFT_PACKAGE_ID} ↔ {DEMO_RIGHT_PACKAGE_ID}
          </div>
        )}
      </div>
    </div>
  );
}
