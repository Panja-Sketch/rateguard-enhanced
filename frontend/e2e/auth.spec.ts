import { expect, test } from '@playwright/test';
import { API_ORIGIN, installMocks, signIn, TEST_PASSWORD } from './support';

test.describe('browser authentication flow', () => {
  test('a signed-out visitor is redirected from a protected route to /login and sees no app content', async ({ page }) => {
    await installMocks(page);
    await page.goto('/missions');
    await expect(page).toHaveURL(/\/login\?next=%2Fmissions$/);
    await expect(page.getByRole('heading', { name: 'Sign in to RateGuard AI' })).toBeVisible();
    await expect(page.getByText('Mission History')).toHaveCount(0);
  });

  test('bad credentials show one generic error (no user enumeration) and stay on /login', async ({ page }) => {
    await installMocks(page, { acceptCredentials: false });
    await page.goto('/login');
    await signIn(page, 'unknown@example.test', 'wrong');
    await expect(page.getByTestId('login-error')).toHaveText('Invalid email or password.');
    await expect(page).toHaveURL(/\/login/);
  });

  test('login attaches the ID token to API calls (header only), restores the requested page, shows the server role, and logout ends the session', async ({ page }) => {
    const { calls } = await installMocks(page, { role: 'VIEWER' });
    await page.goto('/missions');
    await expect(page).toHaveURL(/\/login\?next=%2Fmissions$/);

    await signIn(page);

    // Redirected back to the page originally requested.
    await expect(page).toHaveURL(/\/missions$/);
    await expect(page.getByTestId('session-badge')).toContainText('Viewer');
    // Role-aware navigation (usability only): a VIEWER gets no authoring links.
    await expect(page.getByRole('navigation').getByRole('link', { name: 'Start Mission' })).toHaveCount(0);
    await expect(page.getByRole('navigation').getByRole('link', { name: 'Mission History' })).toBeVisible();

    await expect.poll(() => calls.some((c) => c.url.includes('/api/v1/missions'))).toBe(true);
    const business = calls.filter((c) => c.url.startsWith(`${API_ORIGIN}/api/v1/`));
    expect(business.length).toBeGreaterThan(0);
    for (const call of business) {
      expect(call.authorization).toMatch(/^Bearer [\w-]+\.[\w-]+\.[\w-]+$/);
      // The token is never in a URL, and the password never leaves the sign-in request.
      expect(call.url).not.toMatch(/token|access_token|id_token|eyJ/i);
    }
    expect(business.map((c) => c.authorization).join('')).not.toContain(TEST_PASSWORD);

    // No token or password persisted to localStorage / sessionStorage / cookies.
    const stored = await page.evaluate(() => JSON.stringify({ l: { ...localStorage }, s: { ...sessionStorage }, c: document.cookie }));
    expect(stored).not.toContain(TEST_PASSWORD);
    expect(stored).not.toMatch(/eyJ[\w-]+\.[\w-]+\./);

    // Logout: back to /login, and protected routes redirect again.
    await page.getByRole('button', { name: /Sign out/ }).click();
    await expect(page).toHaveURL(/\/login/);
    await page.goto('/missions');
    await expect(page).toHaveURL(/\/login\?next=%2Fmissions$/);
  });

  test('a RELEASE_OWNER sees the authoring navigation', async ({ page }) => {
    await installMocks(page, { role: 'RELEASE_OWNER' });
    await page.goto('/login');
    await signIn(page);
    const nav = page.getByRole('navigation');
    await expect(nav.getByRole('link', { name: 'Start Mission' })).toBeVisible();
    await expect(nav.getByRole('link', { name: 'Sources' })).toBeVisible();
  });

  test('a 403 from the API is shown as a permission message and does NOT sign the user out', async ({ page }) => {
    await installMocks(page, { missionsStatus: 403 });
    await page.goto('/login?next=%2Fmissions');
    await signIn(page);
    await expect(page.getByText('You do not have permission to perform this action.').first()).toBeVisible();
    await expect(page.getByTestId('session-badge')).toBeVisible();
    await expect(page).toHaveURL(/\/missions$/);
  });

  test('a persistent 401 ends the session (token refresh retried once) and returns to /login without leaking server detail', async ({ page }) => {
    const { calls } = await installMocks(page, { missionsStatus: 401 });
    await page.goto('/login?next=%2Fmissions');
    await signIn(page);
    // The client retried once with a force-refreshed token before giving up ...
    await expect
      .poll(() => calls.filter((c) => c.url.includes('/api/v1/missions')).length, { timeout: 30_000 })
      .toBeGreaterThanOrEqual(2);
    // ... then ended the session: signed out, back at /login for the requested page.
    await expect(page).toHaveURL(/\/login\?next=%2Fmissions/, { timeout: 30_000 });
    await expect(page.getByTestId('session-badge')).toHaveCount(0);
    await expect(page.getByText('token detail that must not be shown')).toHaveCount(0);
  });

  test('an unprovisioned account is told so and offered sign-out (server 403 on /me)', async ({ page }) => {
    await installMocks(page);
    await page.route(`${API_ORIGIN}/api/v1/me`, (route) => {
      if (route.request().method() === 'OPTIONS') {
        return route.fulfill({
          status: 204,
          headers: {
            'access-control-allow-origin': 'http://localhost:3100',
            'access-control-allow-headers': 'authorization,content-type',
            'access-control-allow-methods': 'GET,OPTIONS',
          },
        });
      }
      return route.fulfill({
        status: 403,
        contentType: 'application/json',
        headers: { 'access-control-allow-origin': 'http://localhost:3100' },
        body: JSON.stringify({ detail: { code: 'ACCOUNT_NOT_PROVISIONED', message: 'x' } }),
      });
    });
    await page.goto('/login');
    await signIn(page);
    await expect(page.getByTestId('auth-not-provisioned')).toBeVisible();
    await page.getByTestId('auth-not-provisioned').getByRole('button', { name: 'Sign out' }).click();
    await expect(page).toHaveURL(/\/login/);
  });
});
