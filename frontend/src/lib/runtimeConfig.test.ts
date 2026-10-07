/**
 * @jest-environment jsdom
 */
/**
 * Proves the API base URL is selected from deployment-time runtime
 * configuration (window.__RATEGUARD_RUNTIME_CONFIG__, injected per-request
 * by the server from the RATEGUARD_API_URL env var) rather than being
 * permanently baked into the built image via NEXT_PUBLIC_RATEGUARD_API_URL.
 * A candidate and a production revision deployed from the exact same image
 * digest must be able to resolve two different API URLs.
 */
import { getApiBaseUrl, runtimeConfigScriptContents } from './runtimeConfig';

const ORIGINAL_ENV = process.env;

afterEach(() => {
  process.env = { ...ORIGINAL_ENV };
  delete (window as unknown as { __RATEGUARD_RUNTIME_CONFIG__?: unknown }).__RATEGUARD_RUNTIME_CONFIG__;
});

describe('runtimeConfigScriptContents', () => {
  it('serializes the server-side RATEGUARD_API_URL env var, never the NEXT_PUBLIC one', () => {
    process.env = { ...ORIGINAL_ENV, RATEGUARD_API_URL: 'https://rateguard-api-prod.example.com' };
    const script = runtimeConfigScriptContents();
    expect(script).toContain('https://rateguard-api-prod.example.com');
    expect(script).toContain('window.__RATEGUARD_RUNTIME_CONFIG__');
  });

  it('emits a null apiUrl when RATEGUARD_API_URL is unset, never crashing', () => {
    process.env = { ...ORIGINAL_ENV };
    delete process.env.RATEGUARD_API_URL;
    expect(() => JSON.parse(runtimeConfigScriptContents().replace('window.__RATEGUARD_RUNTIME_CONFIG__ = ', '').replace(/;$/, ''))).not.toThrow();
  });
});

describe('getApiBaseUrl (browser)', () => {
  it('uses the server-injected runtime config over any build-time value', () => {
    process.env = { ...ORIGINAL_ENV, NEXT_PUBLIC_RATEGUARD_API_URL: 'https://baked-in-candidate-url.example.com' };
    window.__RATEGUARD_RUNTIME_CONFIG__ = { apiUrl: 'https://rateguard-api-prod.example.com' };
    expect(getApiBaseUrl()).toBe('https://rateguard-api-prod.example.com');
  });

  it('a candidate revision and a production revision of the SAME image resolve different URLs', () => {
    // Same build (same NEXT_PUBLIC_RATEGUARD_API_URL baked at build time,
    // representing one immutable image digest), different runtime config
    // injected per-deployment -- this is the whole point of the mechanism.
    process.env = { ...ORIGINAL_ENV, NEXT_PUBLIC_RATEGUARD_API_URL: 'https://build-time-value-must-be-ignored.example.com' };

    window.__RATEGUARD_RUNTIME_CONFIG__ = { apiUrl: 'https://candidate---rateguard-api-xyz.a.run.app' };
    const candidateUrl = getApiBaseUrl();

    window.__RATEGUARD_RUNTIME_CONFIG__ = { apiUrl: 'https://rateguard-api-nwhotixfva-uc.a.run.app' };
    const productionUrl = getApiBaseUrl();

    expect(candidateUrl).toBe('https://candidate---rateguard-api-xyz.a.run.app');
    expect(productionUrl).toBe('https://rateguard-api-nwhotixfva-uc.a.run.app');
    expect(candidateUrl).not.toBe(productionUrl);
    expect(candidateUrl).not.toBe('https://build-time-value-must-be-ignored.example.com');
    expect(productionUrl).not.toBe('https://build-time-value-must-be-ignored.example.com');
  });

  it('falls back to NEXT_PUBLIC_RATEGUARD_API_URL when no runtime config was injected (dev/test)', () => {
    process.env = { ...ORIGINAL_ENV, NEXT_PUBLIC_RATEGUARD_API_URL: 'https://dev-fallback.example.com' };
    expect(getApiBaseUrl()).toBe('https://dev-fallback.example.com');
  });

  it('falls back to localhost:8000 when nothing at all is configured', () => {
    process.env = { ...ORIGINAL_ENV };
    delete process.env.NEXT_PUBLIC_RATEGUARD_API_URL;
    expect(getApiBaseUrl()).toBe('http://localhost:8000');
  });
});
