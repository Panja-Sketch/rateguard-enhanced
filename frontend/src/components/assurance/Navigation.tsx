'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { ShieldCheck, Cpu, FileCode2, History, Network, RefreshCw, LogOut } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { fetchHealth } from '@/lib/api/client';
import { useAuth } from '@/lib/auth/AuthProvider';
import { canAuthorReleases, roleLabel } from '@/lib/auth/roles';

export function Navigation() {
  const pathname = usePathname();
  const { status, email, session, signOut } = useAuth();
  const [systemOnline, setSystemOnline] = useState<boolean | null>(null);
  const [checking, setChecking] = useState(false);

  const checkHealth = useCallback(() => {
    setChecking(true);
    fetchHealth()
      .then(() => setSystemOnline(true))
      .catch(() => setSystemOnline(false))
      .finally(() => setChecking(false));
  }, []);

  useEffect(() => {
    checkHealth();
  }, [checkHealth]);

  // Navigation is shown only to a signed-in user. Links a role cannot use are
  // hidden as a usability aid; the API enforces the real permission on every call.
  if (status !== 'signed-in' || pathname === '/login') {
    return null;
  }

  const authors = canAuthorReleases(session?.role);
  const links = [
    { href: '/', label: 'Overview', icon: ShieldCheck },
    ...(authors ? [{ href: '/missions/new', label: 'Start Mission', icon: Cpu }] : []),
    ...(authors ? [{ href: '/sources', label: 'Sources', icon: FileCode2 }] : []),
    { href: '/missions', label: 'Mission History', icon: History },
    { href: '/architecture', label: 'Architecture', icon: Network },
  ];

  return (
    <header className="sticky top-0 z-50 border-b border-slate-800 bg-slate-950/90 backdrop-blur-md">
      <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3 sm:px-6">
        <Link href="/" className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-sky-600 text-white shadow-lg shadow-sky-600/30">
            <ShieldCheck className="h-5 w-5" />
          </div>
          <div>
            <span className="text-lg font-bold tracking-tight text-white">RateGuard <span className="text-sky-400">AI</span></span>
            <span className="ml-2 hidden rounded bg-sky-950 px-2 py-0.5 text-xs font-medium text-sky-300 border border-sky-800 sm:inline-block">Continuous Pricing Assurance</span>
          </div>
        </Link>

        <nav className="flex items-center gap-1 sm:gap-2">
          {links.map((link) => {
            const Icon = link.icon;
            const isActive = pathname === link.href || (link.href !== '/' && pathname.startsWith(link.href));
            return (
              <Link
                key={link.href}
                href={link.href}
                className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs sm:text-sm font-medium transition-colors ${
                  isActive
                    ? 'bg-sky-600/20 text-sky-300 border border-sky-500/30'
                    : 'text-slate-400 hover:bg-slate-850 hover:text-slate-200'
                }`}
              >
                <Icon className="h-4 w-4" />
                <span>{link.label}</span>
              </Link>
            );
          })}
        </nav>

        <div className="hidden items-center gap-2 md:flex">
          <button
            onClick={checkHealth}
            disabled={checking}
            title={systemOnline === false ? 'Click to retry connecting to the RateGuard API' : 'RateGuard API connection status'}
            className="flex items-center gap-1.5 rounded-full bg-slate-900 px-3 py-1 text-xs border border-slate-800 hover:border-slate-700 disabled:opacity-70"
          >
            <span className={`h-2 w-2 rounded-full ${systemOnline === true ? 'bg-emerald-500 animate-pulse' : systemOnline === false ? 'bg-rose-500' : 'bg-amber-500'}`} />
            <span className="text-slate-300 font-mono text-xs">
              {checking ? 'CHECKING...' : systemOnline === true ? 'API LIVE' : systemOnline === false ? 'API OFFLINE' : 'CHECKING...'}
            </span>
            {systemOnline === false && <RefreshCw className="h-3 w-3 text-rose-400" />}
          </button>
          <div className="flex items-center gap-2 text-xs text-slate-400" data-testid="session-badge">
            <span className="max-w-[10rem] truncate" title={email ?? undefined}>{email}</span>
            <span className="rounded bg-slate-900 px-2 py-0.5 border border-slate-800 text-slate-300">{roleLabel(session?.role)}</span>
            <button
              type="button"
              onClick={() => void signOut()}
              className="flex items-center gap-1 rounded-md border border-slate-800 px-2 py-1 text-slate-300 hover:border-slate-600"
            >
              <LogOut className="h-3 w-3" />
              Sign out
            </button>
          </div>
        </div>
      </div>
    </header>
  );
}

