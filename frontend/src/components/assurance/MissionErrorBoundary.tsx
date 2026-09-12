'use client';

import React, { Component, ErrorInfo, ReactNode } from 'react';
import { ShieldAlert, RefreshCw, ArrowLeft } from 'lucide-react';
import Link from 'next/link';

interface Props {
  children: ReactNode;
  onRetry?: () => void;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class MissionErrorBoundary extends Component<Props, State> {
  public state: State = {
    hasError: false,
    error: null,
  };

  public static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('[MissionErrorBoundary] Uncaught render error:', error, errorInfo);
  }

  private handleRetry = () => {
    this.setState({ hasError: false, error: null });
    if (this.props.onRetry) {
      this.props.onRetry();
    }
  };

  public render() {
    if (this.state.hasError) {
      return (
        <div className="max-w-4xl mx-auto py-12 px-4 space-y-6">
          <div className="rounded-2xl border border-rose-800 bg-rose-950/40 p-8 text-rose-100 shadow-2xl space-y-4">
            <div className="flex items-center gap-3">
              <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-rose-900 border border-rose-700 text-rose-200">
                <ShieldAlert className="h-6 w-6" />
              </div>
              <div>
                <h2 className="text-xl font-bold font-sans text-white">Unable to display this mission.</h2>
                <p className="text-xs text-rose-300 mt-0.5">
                  A rendering or data dereferencing exception occurred while presenting mission telemetry.
                </p>
              </div>
            </div>

            {this.state.error && (
              <div className="rounded-lg border border-rose-900 bg-black/60 p-3 font-mono text-xs text-rose-300">
                [Client Exception] {this.state.error.message}
              </div>
            )}

            <div className="flex flex-wrap items-center gap-3 pt-2">
              <button
                onClick={this.handleRetry}
                className="inline-flex items-center gap-1.5 rounded-lg bg-sky-600 px-4 py-2 text-xs font-bold text-white hover:bg-sky-500 transition-all shadow-lg"
              >
                <RefreshCw className="h-4 w-4" /> Retry
              </button>
              <Link
                href="/missions"
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-4 py-2 text-xs font-semibold text-slate-300 hover:bg-slate-800 transition-all"
              >
                <ArrowLeft className="h-4 w-4" /> Back to Assurance History
              </Link>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

