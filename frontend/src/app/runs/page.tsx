'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';

export default function LegacyRunsRedirectPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace('/missions');
  }, [router]);

  return (
    <div className="py-20 text-center text-xs text-slate-400 font-mono">
      Redirecting to Assurance Mission History...
    </div>
  );
}
