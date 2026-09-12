import type { Metadata } from 'next';
import './globals.css';
import { Navigation } from '@/components/assurance/Navigation';

export const metadata: Metadata = {
  title: 'RateGuard AI — Continuous Pricing Assurance for Insurance',
  description: 'Independent agentic pricing assurance, semantic diff, and portfolio risk analysis for insurance carriers.',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="flex min-h-screen flex-col bg-slate-950 text-slate-100 antialiased">
        <Navigation />
        <main className="flex-1 px-4 py-8 sm:px-6 lg:px-8 max-w-7xl mx-auto w-full">
          {children}
        </main>
        <footer className="border-t border-slate-800 bg-slate-950 py-6 text-center text-xs text-slate-500">
          <div className="max-w-7xl mx-auto px-4 flex flex-col sm:flex-row justify-between items-center gap-2">
            <div>RateGuard AI © 2026 — Continuous Pricing Assurance Engine</div>
            <div className="flex gap-4">
              <span>Gemini 3.7 Flash · Google Vertex AI · Google GenAI SDK</span>
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

