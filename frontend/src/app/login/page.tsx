'use client';

import { ShieldCheck } from 'lucide-react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useEffect, useState, type FormEvent } from 'react';
import { useAuth } from '@/lib/auth/AuthProvider';
import { SignInError } from '@/lib/auth/errors';
import { safeNextPath } from '@/lib/auth/redirect';

function LoginForm() {
  const { status, signIn, missingConfig } = useAuth();
  const router = useRouter();
  const params = useSearchParams();
  const next = safeNextPath(params.get('next'));
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (status === 'signed-in') {
      router.replace(next);
    }
  }, [status, next, router]);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await signIn(email, password);
      // Navigation happens in the effect above once the auth state flips.
    } catch (err) {
      setError(err instanceof SignInError ? err.message : 'Sign-in failed. Please try again.');
      setPassword('');
    } finally {
      setSubmitting(false);
    }
  }

  if (status === 'misconfigured') {
    return (
      <div role="alert" className="rounded-lg border border-rose-800 bg-rose-950/40 p-4 text-sm text-rose-200" data-testid="login-misconfigured">
        Sign-in is unavailable: this build is missing its Firebase configuration ({missingConfig.join(', ')}).
      </div>
    );
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4" aria-label="Sign in" data-testid="login-form">
      <div>
        <label htmlFor="email" className="block text-sm font-medium text-slate-300">
          Email
        </label>
        <input
          id="email"
          name="email"
          type="email"
          autoComplete="username"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="mt-1 w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100 focus:border-sky-500 focus:outline-none"
        />
      </div>
      <div>
        <label htmlFor="password" className="block text-sm font-medium text-slate-300">
          Password
        </label>
        <input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="mt-1 w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100 focus:border-sky-500 focus:outline-none"
        />
      </div>
      {error && (
        <p role="alert" className="rounded-md border border-rose-800 bg-rose-950/40 px-3 py-2 text-sm text-rose-200" data-testid="login-error">
          {error}
        </p>
      )}
      <button
        type="submit"
        disabled={submitting || status === 'loading'}
        className="w-full rounded-md bg-sky-600 px-4 py-2 font-medium text-white hover:bg-sky-500 disabled:opacity-60"
      >
        {submitting ? 'Signing in…' : 'Sign in'}
      </button>
    </form>
  );
}

export default function LoginPage() {
  return (
    <div className="mx-auto mt-12 w-full max-w-sm">
      <div className="mb-6 flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-sky-600 text-white">
          <ShieldCheck className="h-5 w-5" />
        </div>
        <div>
          <h1 className="text-xl font-bold text-white">Sign in to RateGuard AI</h1>
          <p className="text-xs text-slate-400">Authorized users only. Synthetic demonstration data.</p>
        </div>
      </div>
      <Suspense fallback={<p className="text-sm text-slate-400">Loading…</p>}>
        <LoginForm />
      </Suspense>
    </div>
  );
}
