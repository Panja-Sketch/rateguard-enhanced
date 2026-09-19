import { getApp, getApps, initializeApp, type FirebaseApp } from 'firebase/app';
import { getAuth, type Auth } from 'firebase/auth';
import { readFirebaseConfig } from './config';

export class FirebaseConfigError extends Error {
  missing: string[];

  constructor(missing: string[]) {
    super('Firebase is not configured for this build.');
    this.name = 'FirebaseConfigError';
    this.missing = missing;
  }
}

/** Returns the Firebase Auth instance, or throws FirebaseConfigError naming the
 * missing NEXT_PUBLIC_FIREBASE_* variables (never their values). */
export function getFirebaseAuth(): Auth {
  const result = readFirebaseConfig();
  if (!result.ok) {
    throw new FirebaseConfigError(result.missing);
  }
  const app: FirebaseApp = getApps().length > 0 ? getApp() : initializeApp(result.config);
  return getAuth(app);
}
