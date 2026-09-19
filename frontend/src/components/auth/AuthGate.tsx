'use client';

import { usePathname, useRouter } from 'next/navigation';
import { useEffect, type ReactNode } from 'react';
import { useAuth } from '@/lib/auth/AuthProvider';

/**
 * Protects every application route: signed-out visitors are redirected to
 * /login (preserving a safe `next` path); the /login page itself is public.
 * This is a UX guard — the API independently rejects unauthenticated calls.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const { status, sessionProblem, signOut, missingConfig } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const isLogin = pathname === '/login';

  useEffect(() => {
    if (status === 'signed-out' && !isLogin) {
      router.replace(`/login?next=${encodeURIComponent(pathname || '/')}`);
    }
  }, [status, isLogin, pathname, router]);

  if (isLogin) {
    return <>{children}</>;
  }

  if (status === 'loading') {
    return (
      <div role="status" aria-live="polite" className="py-24 text-center text-sm text-slate-400" data-testid="auth-loading">
        Checking your session…
      </div>
    );
  }

  if (status === 'misconfigured') {
    return (
      <div role="alert" className="mx-auto max-w-lg rounded-lg border border-rose-800 bg-rose-950/40 p-6 text-sm text-rose-200" data-testid="auth-misconfigured">
        <p className="font-semibold">Sign-in is unavailable</p>
        <p className="mt-2">
          This build is missing its Firebase web configuration ({missingConfig.join(', ')}). Set the documented
          NEXT_PUBLIC_FIREBASE_* variables and rebuild.
        </p>
      </div>
    );
  }

  if (status === 'signed-out') {
    // The effect above is redirecting; render nothing meanwhile so no
    // protected content flashes.
    return null;
  }

  if (sessionProblem === 'not-provisioned') {
    return (
      <div role="alert" className="mx-auto max-w-lg rounded-lg border border-amber-700 bg-amber-950/40 p-6 text-sm text-amber-200" data-testid="auth-not-provisioned">
        <p className="font-semibold">Your account is not set up for RateGuard</p>
        <p className="mt-2">You are signed in, but an administrator has not assigned you a role yet. Contact your administrator.</p>
        <button
          type="button"
          onClick={() => void signOut()}
          className="mt-4 rounded-md border border-amber-600 px-3 py-1.5 text-amber-100 hover:bg-amber-900/40"
        >
          Sign out
        </button>
      </div>
    );
  }

  return <>{children}</>;
}
