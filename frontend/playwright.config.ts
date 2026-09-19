import { defineConfig } from '@playwright/test';

/**
 * Browser authentication-flow tests.
 *
 * No live Firebase and no live backend are used: the Firebase Auth REST
 * endpoints and the RateGuard API are intercepted in the browser (see
 * e2e/support.ts). The Firebase web config below is a set of dummy public
 * identifiers — it exists only so the SDK initializes.
 *
 * Uses the machine's installed Chrome (`channel: 'chrome'`) so no browser
 * download is required; set PLAYWRIGHT_CHANNEL=msedge (or unset the channel
 * after `npx playwright install chromium`) on machines without Chrome.
 */
const PORT = 3100;

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: `http://localhost:${PORT}`,
    channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome',
    trace: 'off',
  },
  webServer: {
    command: `npx next dev -p ${PORT}`,
    url: `http://localhost:${PORT}/login`,
    reuseExistingServer: false,
    timeout: 240_000,
    env: {
      NEXT_PUBLIC_RATEGUARD_API_URL: 'http://127.0.0.1:8100',
      NEXT_PUBLIC_FIREBASE_API_KEY: 'e2e-dummy-api-key',
      NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN: 'e2e-project.firebaseapp.com',
      NEXT_PUBLIC_FIREBASE_PROJECT_ID: 'e2e-project',
      NEXT_PUBLIC_FIREBASE_APP_ID: '1:1:web:e2e',
      NEXT_TELEMETRY_DISABLED: '1',
    },
  },
});
