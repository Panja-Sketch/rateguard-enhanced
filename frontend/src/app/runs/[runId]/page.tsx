'use client';

import { useEffect } from 'react';
import { useRouter, useParams } from 'next/navigation';

export default function LegacyRunDetailRedirectPage() {
  const router = useRouter();
  const params = useParams();
  const runId = params.runId as string;

  useEffect(() => {
    if (runId) {
      router.replace(`/missions/${runId}`);
    }
  }, [router, runId]);

  return (
    <div className="py-20 text-center text-xs text-slate-400 font-mono">
      Redirecting to Assurance Mission Detail...
    </div>
  );
}
