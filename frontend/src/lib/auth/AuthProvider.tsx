'use client';

import {
  onIdTokenChanged,
  signInWithEmailAndPassword,
  signOut as firebaseSignOut,
  type Auth,
} from 'firebase/auth';
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { ApiError, fetchSession, type SessionInfo } from '../api/client';
import { FirebaseConfigError, getFirebaseAuth } from '../firebase/client';
import { describeSignInError, SignInError } from './errors';
import { registerAuthHooks } from './session';

export type AuthStatus = 'loading' | 'signed-out' | 'signed-in' | 'misconfigured';
export type SessionProblem = 'not-provisioned' | 'unavailable' | null;

export interface AuthContextValue {
  status: AuthStatus;
  /** Display email from Firebase (informational only). */
  email: string | null;
  /** The server's view of the caller (role/tenant). Navigation hints only. */
  session: SessionInfo | null;
  sessionProblem: SessionProblem;
  /** Names of missing NEXT_PUBLIC_FIREBASE_* variables when misconfigured. */
  missingConfig: string[];
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used inside <AuthProvider>.');
  }
  return ctx;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>('loading');
  const [email, setEmail] = useState<string | null>(null);
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [sessionProblem, setSessionProblem] = useState<SessionProblem>(null);
  const [missingConfig, setMissingConfig] = useState<string[]>([]);
  const authRef = useRef<Auth | null>(null);
  const lastUidRef = useRef<string | null>(null);

  const signOut = useCallback(async () => {
    const auth = authRef.current;
    if (auth) {
      await firebaseSignOut(auth);
    }
  }, []);

  useEffect(() => {
    let auth: Auth;
    try {
      auth = getFirebaseAuth();
    } catch (err) {
      if (err instanceof FirebaseConfigError) {
        setMissingConfig(err.missing);
        setStatus('misconfigured');
        return;
      }
      throw err;
    }
    authRef.current = auth;

    // The API client asks for tokens through these hooks. The Firebase SDK
    // refreshes an expired ID token transparently on getIdToken(); a forced
    // refresh is used by the client after a 401. A persistent 401 signs out.
    const unregister = registerAuthHooks(
      async (forceRefresh) => (auth.currentUser ? auth.currentUser.getIdToken(forceRefresh) : null),
      () => {
        void firebaseSignOut(auth);
      },
    );

    const unsubscribe = onIdTokenChanged(auth, async (user) => {
      if (!user) {
        lastUidRef.current = null;
        setEmail(null);
        setSession(null);
        setSessionProblem(null);
        setStatus('signed-out');
        return;
      }
      setEmail(user.email);
      // Token refreshes (hourly) also fire this callback; only re-resolve the
      // server-side session when the signed-in user actually changed.
      if (lastUidRef.current !== user.uid) {
        lastUidRef.current = user.uid;
        setSession(null);
        setSessionProblem(null);
        try {
          setSession(await fetchSession());
        } catch (err) {
          setSessionProblem(err instanceof ApiError && err.status === 403 ? 'not-provisioned' : 'unavailable');
        }
      }
      setStatus('signed-in');
    });

    return () => {
      unsubscribe();
      unregister();
    };
  }, []);

  const signIn = useCallback(async (emailInput: string, password: string) => {
    const auth = authRef.current;
    if (!auth) {
      throw new SignInError('Sign-in is not available: this deployment is missing its Firebase configuration.');
    }
    try {
      await signInWithEmailAndPassword(auth, emailInput.trim(), password);
    } catch (err) {
      const code = typeof err === 'object' && err !== null && 'code' in err ? String((err as { code: unknown }).code) : undefined;
      throw new SignInError(describeSignInError(code));
    }
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ status, email, session, sessionProblem, missingConfig, signIn, signOut }),
    [status, email, session, sessionProblem, missingConfig, signIn, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
