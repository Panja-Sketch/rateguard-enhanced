/**
 * Public Firebase web configuration.
 *
 * These values are identifiers that ship in the browser bundle by design; they
 * are NOT secrets and grant no server access (the API verifies every ID token).
 * They are read only from documented NEXT_PUBLIC_FIREBASE_* variables — never
 * hard-coded here — and each is referenced statically so Next.js can inline it
 * at build time (dynamic `process.env[name]` access would not be inlined).
 */

export interface FirebaseWebConfig {
  apiKey: string;
  authDomain: string;
  projectId: string;
  storageBucket?: string;
  messagingSenderId?: string;
  appId: string;
  measurementId?: string;
}

export type FirebaseConfigResult =
  | { ok: true; config: FirebaseWebConfig }
  | { ok: false; missing: string[] };

/** Variables that must be present for sign-in to work. */
const REQUIRED = ['NEXT_PUBLIC_FIREBASE_API_KEY', 'NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN', 'NEXT_PUBLIC_FIREBASE_PROJECT_ID', 'NEXT_PUBLIC_FIREBASE_APP_ID'] as const;

export function readFirebaseConfig(
  env: Record<string, string | undefined> = {
    NEXT_PUBLIC_FIREBASE_API_KEY: process.env.NEXT_PUBLIC_FIREBASE_API_KEY,
    NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN,
    NEXT_PUBLIC_FIREBASE_PROJECT_ID: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID,
    NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET: process.env.NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET,
    NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID: process.env.NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID,
    NEXT_PUBLIC_FIREBASE_APP_ID: process.env.NEXT_PUBLIC_FIREBASE_APP_ID,
    NEXT_PUBLIC_FIREBASE_MEASUREMENT_ID: process.env.NEXT_PUBLIC_FIREBASE_MEASUREMENT_ID,
  },
): FirebaseConfigResult {
  const missing = REQUIRED.filter((name) => !env[name] || !env[name]!.trim());
  if (missing.length > 0) {
    return { ok: false, missing: [...missing] };
  }
  return {
    ok: true,
    config: {
      apiKey: env.NEXT_PUBLIC_FIREBASE_API_KEY!,
      authDomain: env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN!,
      projectId: env.NEXT_PUBLIC_FIREBASE_PROJECT_ID!,
      storageBucket: env.NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET || undefined,
      messagingSenderId: env.NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID || undefined,
      appId: env.NEXT_PUBLIC_FIREBASE_APP_ID!,
      measurementId: env.NEXT_PUBLIC_FIREBASE_MEASUREMENT_ID || undefined,
    },
  };
}
