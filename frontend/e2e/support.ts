import type { Page, Route } from '@playwright/test';

export const API_ORIGIN = 'http://127.0.0.1:8100';
const WEB_ORIGIN = 'http://localhost:3100';

// Not real credentials: values the intercepted Firebase endpoints accept in these tests only.
export const TEST_EMAIL = 'e2e-user@example.test';
export const TEST_PASSWORD = 'e2e-only-password';

function b64url(obj: unknown): string {
  return Buffer.from(JSON.stringify(obj)).toString('base64url');
}

/** An unsigned, structurally valid Firebase-style ID token (the browser SDK
 * only parses it; the real backend would verify the signature). */
export function fakeIdToken(seq: number): string {
  const now = Math.floor(Date.now() / 1000);
  return [
    b64url({ alg: 'RS256', kid: 'e2e', typ: 'JWT' }),
    b64url({
      iss: 'https://securetoken.google.com/e2e-project',
      aud: 'e2e-project',
      sub: 'e2e-uid',
      user_id: 'e2e-uid',
      email: TEST_EMAIL,
      iat: now,
      auth_time: now,
      exp: now + 3600,
      firebase: { identities: {}, sign_in_provider: 'password' },
      seq,
    }),
    'e2e-signature',
  ].join('.');
}

export interface ApiCall {
  method: string;
  url: string;
  authorization: string | null;
}

export interface MockOptions {
  /** Status for GET /api/v1/missions (default 200). */
  missionsStatus?: number;
  /** Role returned by GET /api/v1/me. */
  role?: 'ADMIN' | 'RELEASE_OWNER' | 'CONSUMER_REVIEWER' | 'VIEWER';
  /** Whether the Firebase sign-in endpoint accepts the credentials. */
  acceptCredentials?: boolean;
}

/** Intercepts Firebase Auth REST calls and the RateGuard API; records every API call. */
export async function installMocks(page: Page, opts: MockOptions = {}): Promise<{ calls: ApiCall[]; firebaseUrls: string[] }> {
  const calls: ApiCall[] = [];
  const firebaseUrls: string[] = [];
  let tokenSeq = 0;
  const accept = opts.acceptCredentials ?? true;

  const json = (route: Route, status: number, body: unknown, extra: Record<string, string> = {}) =>
    route.fulfill({
      status,
      contentType: 'application/json',
      headers: {
        'access-control-allow-origin': WEB_ORIGIN,
        'access-control-allow-headers': 'authorization,content-type',
        'access-control-allow-methods': 'GET,POST,DELETE,OPTIONS',
        ...extra,
      },
      body: JSON.stringify(body),
    });

  await page.route(/identitytoolkit\.googleapis\.com/, async (route) => {
    const url = route.request().url();
    firebaseUrls.push(url);
    if (url.includes('accounts:signInWithPassword')) {
      if (!accept) {
        return json(route, 400, { error: { code: 400, message: 'INVALID_LOGIN_CREDENTIALS', errors: [] } });
      }
      tokenSeq += 1;
      return json(route, 200, {
        kind: 'identitytoolkit#VerifyPasswordResponse',
        localId: 'e2e-uid',
        email: TEST_EMAIL,
        registered: true,
        idToken: fakeIdToken(tokenSeq),
        refreshToken: 'e2e-refresh-token',
        expiresIn: '3600',
      });
    }
    if (url.includes('accounts:lookup')) {
      return json(route, 200, {
        kind: 'identitytoolkit#GetAccountInfoResponse',
        users: [
          {
            localId: 'e2e-uid',
            email: TEST_EMAIL,
            emailVerified: true,
            providerUserInfo: [{ providerId: 'password', federatedId: TEST_EMAIL, email: TEST_EMAIL, rawId: TEST_EMAIL }],
            lastLoginAt: String(Date.now()),
            createdAt: String(Date.now()),
          },
        ],
      });
    }
    return json(route, 200, {});
  });

  await page.route(/securetoken\.googleapis\.com/, async (route) => {
    firebaseUrls.push(route.request().url());
    tokenSeq += 1;
    return json(route, 200, {
      access_token: fakeIdToken(tokenSeq),
      id_token: fakeIdToken(tokenSeq),
      refresh_token: 'e2e-refresh-token',
      expires_in: '3600',
      token_type: 'Bearer',
      user_id: 'e2e-uid',
      project_id: '1',
    });
  });

  await page.route(`${API_ORIGIN}/**`, async (route) => {
    const request = route.request();
    if (request.method() === 'OPTIONS') {
      return json(route, 204, {});
    }
    const url = new URL(request.url());
    calls.push({ method: request.method(), url: request.url(), authorization: request.headers()['authorization'] ?? null });

    if (url.pathname === '/health') return json(route, 200, { status: 'healthy' });
    if (url.pathname === '/api/v1/me') {
      return json(route, 200, { uid: 'e2e-uid', email: TEST_EMAIL, tenant_id: 'rateguard-demo', role: opts.role ?? 'RELEASE_OWNER' });
    }
    if (url.pathname === '/api/v1/missions' && request.method() === 'GET') {
      const status = opts.missionsStatus ?? 200;
      if (status === 401) return json(route, 401, { detail: { code: 'INVALID_TOKEN', message: 'token detail that must not be shown' } });
      if (status === 403) return json(route, 403, { detail: { code: 'INSUFFICIENT_ROLE', message: 'server side text' } });
      return json(route, 200, { missions: [], total_count: 0, limit: 50, offset: 0 });
    }
    return json(route, 404, { detail: 'not found' });
  });

  return { calls, firebaseUrls };
}

export async function signIn(page: Page, email = TEST_EMAIL, password = TEST_PASSWORD): Promise<void> {
  await page.getByLabel('Email').fill(email);
  await page.getByLabel('Password').fill(password);
  await page.getByRole('button', { name: 'Sign in' }).click();
}
