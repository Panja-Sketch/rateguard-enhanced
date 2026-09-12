'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';

export default function DemoRedirectPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace('/missions/new');
  }, [router]);

  return (
    <div className="py-20 text-center text-xs text-slate-400 font-mono">
      Redirecting to Start Assurance Mission...
    </div>
  );
}
