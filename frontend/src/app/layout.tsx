import type { Metadata } from 'next';
import './globals.css';
import { Navigation } from '@/components/assurance/Navigation';
import { AuthGate } from '@/components/auth/AuthGate';
import { AuthProvider } from '@/lib/auth/AuthProvider';
import { runtimeConfigScriptContents } from '@/lib/runtimeConfig';

export const metadata: Metadata = {
  title: 'RateGuard AI — Continuous Pricing Assurance for Insurance',
  description: 'Independent agentic pricing assurance, semantic diff, and portfolio risk analysis for insurance carriers.',
};

// The API base URL must be resolved from the RATEGUARD_API_URL server env
// var on every request (see lib/runtimeConfig.ts), not baked in at build
// time — force-dynamic disables static prerendering of this layout so that
// read never gets frozen into a build-time snapshot.
export const dynamic = 'force-dynamic';

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <head>
        <script
          id="rateguard-runtime-config"
          // eslint-disable-next-line react/no-danger
          dangerouslySetInnerHTML={{ __html: runtimeConfigScriptContents() }}
        />
      </head>
      <body className="flex min-h-screen flex-col bg-slate-950 text-slate-100 antialiased">
        <AuthProvider>
          <Navigation />
          <main className="flex-1 px-4 py-8 sm:px-6 lg:px-8 max-w-7xl mx-auto w-full">
            <AuthGate>{children}</AuthGate>
          </main>
        </AuthProvider>
        <footer className="border-t border-slate-800 bg-slate-950 py-6 text-center text-xs text-slate-500">
          <div className="max-w-7xl mx-auto px-4 flex flex-col sm:flex-row justify-between items-center gap-2">
            <div>RateGuard AI © 2026 — Continuous Pricing Assurance Engine</div>
            <div className="flex gap-4">
              <span>Gemini 3.1 Flash-Lite · Google Vertex AI · Google GenAI SDK</span>
              <span>BigQuery 50K Analytics</span>
              <span>Firestore RunState</span>
              <span>GCS Artifacts</span>
            </div>
          </div>
        </footer>
      </body>
    </html>
  );
}

