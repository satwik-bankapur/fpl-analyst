"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

// "Refresh my brief" (§2.2): re-fetches the server component, which re-hits the
// GET route -> cache/input-hash decides whether the graph actually reruns.
export default function RefreshButton({ className = "" }: { className?: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  return (
    <button
      type="button"
      disabled={busy}
      onClick={() => {
        setBusy(true);
        router.refresh();
        setTimeout(() => setBusy(false), 1200);
      }}
      className={`btn-ghost ${className}`}
    >
      {busy ? "Refreshing…" : "Refresh my brief"}
    </button>
  );
}
