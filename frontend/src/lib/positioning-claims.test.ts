import fs from 'fs';
import path from 'path';

// This suite does not render React components (no @testing-library/react /
// jsdom is installed in this project — see jest.config.js). Instead it
// source-checks the customer-facing pages this session touched for the
// specific prohibited/required claim language locked in
// docs/architecture/RATEGUARD_LOCKED_SOURCE_OF_TRUTH.md section 2 and the
// PricingCenter-complementary positioning brief. It is a claims-policy
// regression test, not a rendering test.

const APP_DIR = path.join(__dirname, '..', 'app');

function readSource(relativePath: string): string {
  return fs.readFileSync(path.join(APP_DIR, relativePath), 'utf-8');
}

// JSX text nodes wrap across source lines with indentation, so a literal
// multi-word sentence in the rendered UI is not a contiguous substring of
// the raw .tsx file. Collapsing all whitespace runs to a single space lets
// these tests assert against the sentence as a reader/browser would see it.
function normalizeWhitespace(text: string): string {
  return text.replace(/\s+/g, ' ').trim();
}

const CUSTOMER_FACING_FILES = [
  'page.tsx',
  'positioning/page.tsx',
  'architecture/page.tsx',
  'sources/page.tsx',
];

// Phrases the locked doc section 2.2 explicitly prohibits, or that this
// session's brief calls out as violations to remove. Matching is
// case-insensitive and deliberately loose (substrings), so a future edit
// that reintroduces one of these trips the test rather than being missed.
const PROHIBITED_SUBSTRINGS = [
  '100% accurate',
  'regulator approved',
  'legally compliant',
  'guarantees fair pricing',
  'hash-chained',
  'hash chained',
  'immutable ledger',
  'blockchain',
  'detects demographic bias',
  'proves unfair discrimination',
  'live production renewal integration',
];

describe('customer-facing pages never contain a prohibited claim', () => {
  for (const file of CUSTOMER_FACING_FILES) {
    it(`${file} contains none of the locked-doc-prohibited phrases`, () => {
      const source = readSource(file).toLowerCase();
      for (const phrase of PROHIBITED_SUBSTRINGS) {
        expect(source).not.toContain(phrase.toLowerCase());
      }
    });
  }
});

describe('sources/page.tsx and docs never assert a built named-vendor adapter', () => {
  it('does not claim an auto-detected Guidewire/Duck Creek wrapper is compiled', () => {
    const source = readSource('sources/page.tsx');
    // The specific violation this session found and fixed: a claim that a
    // "Guidewire/Duck Creek-style wrapper is also auto-detected and
    // compiled deterministically" implied a built, tested adapter that does
    // not exist. Guard against it being reintroduced.
    expect(source).not.toMatch(/guidewire\/duck creek-style wrapper is also auto-detected/i);
  });

  it('frames any Guidewire/Duck Creek mention in terms of the connector contract, not a built adapter', () => {
    const source = readSource('sources/page.tsx');
    const mentionsGuidewire = /guidewire|duck creek/i.test(source);
    if (mentionsGuidewire) {
      // The file overall must frame Guidewire/Duck Creek in terms of the
      // vendor-neutral connector contract rather than a built adapter.
      expect(source.toLowerCase()).toMatch(/connector contract|connector/);
    } else {
      // Removing the mention entirely also satisfies "no built-adapter claim".
      expect(mentionsGuidewire).toBe(false);
    }
  });
});

describe('positioning page carries the locked positioning sentence and required sections', () => {
  const source = readSource('positioning/page.tsx');

  it('states the locked one-sentence positioning', () => {
    const normalized = normalizeWhitespace(source);
    expect(normalized).toContain('PricingCenter is where insurers author and configure rates.');
    expect(normalized).toContain(
      'RateGuard complements Guidewire, Duck Creek, legacy platforms, and custom rating engines; it does not replace them.'
    );
  });

  it('includes an honest market-boundary statement (not every buyer needs RateGuard)', () => {
    expect(source.toLowerCase()).toContain('optional');
  });

  it('includes a "what RateGuard does not do" section', () => {
    expect(source).toContain('What RateGuard Does Not Do');
  });

  it('describes the evidence bundle as hashed-with-a-manifest, never hash-chained', () => {
    expect(source.toLowerCase()).toContain('sha-256 hashed');
    expect(source.toLowerCase()).not.toContain('hash-chained');
  });
});

describe('homepage carries the seven-step customer-facing narrative order', () => {
  const source = readSource('page.tsx');

  it('includes all seven narrative steps in order', () => {
    const steps = [
      'Approved pricing intent enters',
      'Candidate implementation is reached',
      'Targeted probes are generated',
      'approved intent is compared',
      'portfolio impact is quantified',
      'release decision is returned',
      'Customer impact and evidence are produced',
    ];
    let searchFrom = 0;
    for (const step of steps) {
      const idx = source.toLowerCase().indexOf(step.toLowerCase(), searchFrom);
      expect(idx).toBeGreaterThanOrEqual(0);
      searchFrom = idx + step.length;
    }
  });

  it('visually distinguishes the pricing platform from RateGuard', () => {
    expect(source).toContain('The pricing platform');
    expect(source.toLowerCase()).toContain('independently verifies the deployed result');
  });
});
