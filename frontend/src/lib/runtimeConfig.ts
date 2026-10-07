/**
 * Deployment-time (not build-time) API base URL resolution.
 *
 * NEXT_PUBLIC_RATEGUARD_API_URL is inlined into the client JS bundle at
 * `next build` time, so a single built image can only ever call one API URL
 * — which meant a web image built for the candidate-tagged API URL could
 * never be safely promoted to production (a later candidate tag move could
 * silently redirect production traffic to a stale/experimental backend).
 *
 * Instead, the SAME immutable image digest is deployed unchanged for both
 * the candidate and the production revision; only the Cloud Run env var
 * `RATEGUARD_API_URL` (a plain server-side runtime env var, never
 * NEXT_PUBLIC_*, so it is never baked into client JS) differs between them.
 * The root layout (a dynamically-rendered Server Component, see
 * `export const dynamic` in layout.tsx) reads that env var on every request
 * and injects it into the page as `window.__RATEGUARD_RUNTIME_CONFIG__`
 * before any client script runs. `getApiBaseUrl()` reads that global in the
 * browser, and reads the same server-side env var directly during SSR.
 *
 * Falls back to NEXT_PUBLIC_RATEGUARD_API_URL (kept only for `next dev` /
 * tests where no server-injected runtime config exists) and finally to
 * localhost:8000.
 */

declare global {
  interface Window {
    __RATEGUARD_RUNTIME_CONFIG__?: { apiUrl?: string | null };
  }
}

const DEFAULT_API_URL = 'http://localhost:8000';

export function runtimeConfigScriptContents(): string {
  const apiUrl = process.env.RATEGUARD_API_URL || null;
  return `window.__RATEGUARD_RUNTIME_CONFIG__ = ${JSON.stringify({ apiUrl })};`;
}

export function getApiBaseUrl(): string {
  if (typeof window !== 'undefined') {
    const injected = window.__RATEGUARD_RUNTIME_CONFIG__?.apiUrl;
    if (injected) return injected;
    return process.env.NEXT_PUBLIC_RATEGUARD_API_URL || DEFAULT_API_URL;
  }
  // Server-side rendering: the runtime env var is available directly, with
  // no request-relative global to read.
  return process.env.RATEGUARD_API_URL || process.env.NEXT_PUBLIC_RATEGUARD_API_URL || DEFAULT_API_URL;
}
