"use client";

import dynamic from "next/dynamic";

// Charts touch `window`; render the whole terminal client-side only.
const Terminal = dynamic(() => import("@/components/Terminal"), {
  ssr: false,
  loading: () => (
    <div className="h-screen flex items-center justify-center text-muted text-sm">
      <span className="font-bold text-text">
        Fin<span className="text-accent">Ally</span>
      </span>
      <span className="ml-3 text-faint">loading terminal…</span>
    </div>
  ),
});

export default function Page() {
  return <Terminal />;
}
