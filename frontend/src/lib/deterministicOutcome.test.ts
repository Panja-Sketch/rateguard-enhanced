import { describeDeterministicOutcome, notInvokedExplanation } from './deterministicOutcome';

const inconclusive = (cls: string | null = 'CONNECTOR_UNAVAILABLE') => ({ outcome: 'INCONCLUSIVE', inconclusive_class: cls });

describe('describeDeterministicOutcome', () => {
  it('never claims zero diffs when no valid comparison completed', () => {
    const summary = {
      match_count: 0,
      mismatch_count: 0,
      inconclusive_count: 11,
      experiments: Array.from({ length: 11 }, () => inconclusive()),
    };
    const out = describeDeterministicOutcome(summary);
    expect(out.noValidComparisons).toBe(true);
    expect(out.message).toContain('No valid premium comparisons were completed because the connector was unavailable');
    expect(out.message).not.toMatch(/zero/i);
    expect(notInvokedExplanation(summary)).not.toMatch(/zero/i);
    expect(notInvokedExplanation(summary)).toContain('No valid premium comparisons were completed');
  });

  it('names the failure class without technical detail', () => {
    const auth = describeDeterministicOutcome({
      inconclusive_count: 2,
      experiments: [inconclusive('CONNECTOR_AUTH_DENIED'), inconclusive('CONNECTOR_AUTH_DENIED')],
    });
    expect(auth.message).toContain('the connector rejected the service identity');
    const timeout = describeDeterministicOutcome({ inconclusive_count: 1, experiments: [inconclusive('CONNECTOR_TIMEOUT')] });
    expect(timeout.message).toContain('the connector timed out');
    expect(auth.message).not.toMatch(/https?:|token|bearer/i);
  });

  it('does not blame the connector for non-connector inconclusive probes', () => {
    const out = describeDeterministicOutcome({ inconclusive_count: 1, experiments: [{ outcome: 'INCONCLUSIVE' }] });
    expect(out.message).not.toContain('connector');
    expect(out.message).toContain('No valid premium comparisons were completed');
  });

  it('reports zero diffs only when every probe was a valid match', () => {
    const out = describeDeterministicOutcome({ match_count: 11, mismatch_count: 0, inconclusive_count: 0 });
    expect(out.message).toContain('zero deterministic diffs');
    expect(notInvokedExplanation({ match_count: 11 })).toContain('zero deterministic diffs');
  });

  it('reports mismatches, and mixed results, accurately', () => {
    expect(describeDeterministicOutcome({ match_count: 8, mismatch_count: 3, inconclusive_count: 0 }).message).toContain(
      '3 premium mismatches',
    );
    const mixed = describeDeterministicOutcome({ match_count: 4, mismatch_count: 0, inconclusive_count: 2 });
    expect(mixed.noValidComparisons).toBe(false);
    expect(mixed.message).toContain('2 of 6 probes were inconclusive');
    expect(mixed.message).not.toContain('zero');
  });
});
