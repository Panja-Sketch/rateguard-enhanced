/**
 * Accurate wording for what the deterministic probes established.
 *
 * "Zero diffs" is only true when valid comparisons were actually made. When every
 * probe was inconclusive (for example the connector could not be reached), no
 * comparison completed, so the UI must say that instead of implying that nothing
 * differed. Wording never includes endpoints, tokens or payloads.
 */

export interface ExperimentLike {
  outcome?: string;
  inconclusive_class?: string | null;
}

export interface ExperimentsSummary {
  match_count?: number;
  mismatch_count?: number;
  inconclusive_count?: number;
  // Loosely typed on purpose: the mission page passes the API's experiment rows.
  experiments?: readonly unknown[];
}

const CLASS_DETAIL: Record<string, string> = {
  CONNECTOR_AUTH_DENIED: 'the connector rejected the service identity',
  CONNECTOR_TIMEOUT: 'the connector timed out',
  CONNECTOR_CONTRACT_ERROR: 'the connector response did not satisfy the versioned contract',
  CONNECTOR_VERSION_UNSUPPORTED: 'the requested engine version is not supported',
  CONNECTOR_UNAVAILABLE: 'the connector was unavailable',
};

export interface DeterministicOutcome {
  /** Number of probes that produced a valid premium comparison (match or mismatch). */
  validComparisons: number;
  inconclusive: number;
  mismatches: number;
  /** True when probes ran but none produced a valid comparison. */
  noValidComparisons: boolean;
  /** One accurate sentence describing the deterministic result. */
  message: string;
}

export function describeDeterministicOutcome(summary: ExperimentsSummary | null | undefined): DeterministicOutcome {
  const experiments = (summary?.experiments ?? []) as ExperimentLike[];
  const countOf = (o: string) => experiments.filter((e) => e.outcome === o).length;
  const matches = summary?.match_count ?? countOf('MATCH');
  const mismatches = summary?.mismatch_count ?? countOf('MISMATCH');
  const inconclusive = summary?.inconclusive_count ?? countOf('INCONCLUSIVE');
  const valid = matches + mismatches;

  if (valid === 0 && inconclusive > 0) {
    const classes = Array.from(
      new Set(
        experiments.filter((e) => e.outcome === 'INCONCLUSIVE' && e.inconclusive_class).map((e) => e.inconclusive_class as string),
      ),
    );
    const connectorRelated = classes.length > 0;
    const detail = classes.length === 1 ? CLASS_DETAIL[classes[0]] : undefined;
    const message = connectorRelated
      ? `No valid premium comparisons were completed because the connector was unavailable${detail && detail !== CLASS_DETAIL.CONNECTOR_UNAVAILABLE ? ` (${detail})` : ''}. This is not evidence of a pricing defect.`
      : 'No valid premium comparisons were completed; every probe was inconclusive. This is not evidence of a pricing defect.';
    return { validComparisons: 0, inconclusive, mismatches: 0, noValidComparisons: true, message };
  }
  if (mismatches > 0) {
    return {
      validComparisons: valid,
      inconclusive,
      mismatches,
      noValidComparisons: false,
      message: `${mismatches} premium mismatch${mismatches === 1 ? '' : 'es'} were established deterministically${inconclusive > 0 ? `; ${inconclusive} probe${inconclusive === 1 ? ' was' : 's were'} inconclusive` : ''}.`,
    };
  }
  if (inconclusive > 0) {
    return {
      validComparisons: valid,
      inconclusive,
      mismatches: 0,
      noValidComparisons: false,
      message: `${inconclusive} of ${valid + inconclusive} probes were inconclusive; the ${valid} completed comparison${valid === 1 ? '' : 's'} showed no differences.`,
    };
  }
  return {
    validComparisons: valid,
    inconclusive: 0,
    mismatches: 0,
    noValidComparisons: false,
    message: valid > 0 ? `All ${valid} valid premium comparisons matched: zero deterministic diffs.` : 'No probes were executed.',
  };
}

/** Text for the AI-runtime line when Gemini was not invoked by design. */
export function notInvokedExplanation(summary: ExperimentsSummary | null | undefined): string {
  const outcome = describeDeterministicOutcome(summary);
  if (outcome.noValidComparisons || outcome.validComparisons === 0) {
    return `Gemini not invoked by design. ${outcome.message}`;
  }
  return `Gemini not invoked by design — ${outcome.message} No decision required AI judgment.`;
}
