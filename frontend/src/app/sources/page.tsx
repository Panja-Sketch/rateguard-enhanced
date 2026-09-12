'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { ApiError, uploadSourceFile, compileSource, createAssuranceMission, describeFetchError, CompilationReceipt } from '@/lib/api/client';
import { SourceDescriptor, ValidationIssue } from '@/lib/types/assurance';
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
  const [error, setError] = useState<string | null>(null);
  const [fieldIssuesA, setFieldIssuesA] = useState<ValidationIssue[]>([]);
  const [fieldIssuesB, setFieldIssuesB] = useState<ValidationIssue[]>([]);

  // Explicit opt-in only — real uploaded sources are never silently replaced with
  // the bundled Arizona demo packages.
  const [useDemoSample, setUseDemoSample] = useState(false);

  const hasRealSources = !!(compiledA?.ipir_package_id && compiledB?.ipir_package_id);
  const canExecute = hasRealSources || useDemoSample;

  const metadataMismatch =
    hasRealSources &&
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
      const sourceBRef = hasRealSources
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
        name: 'Assurance Mission Launched from Sources',
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
          output in the file you upload is what RateGuard actually compiles. A platform rating-config JSON
          export (e.g. Guidewire/Duck Creek-style wrapper) is also auto-detected and compiled deterministically.
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
              accept=".json,application/json"
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
        </div>

        {/* Source B: Target Engine Implementation */}
        <div className="rounded-2xl border border-purple-800/60 bg-slate-900/80 p-5 space-y-4 shadow-xl">
          <div className="flex items-center justify-between">
            <span className="rounded bg-purple-950 px-2.5 py-0.5 text-xs font-bold text-purple-300 border border-purple-800">
              Source B
            </span>
            <span className="text-xs text-slate-400 font-mono">Implementation</span>
          </div>

          <form onSubmit={handleUploadB} className="space-y-3">
            <input
              type="file"
              accept=".json,application/json"
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
