"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

// "Refresh my brief" (§2.2): re-fetches the server component, which re-hits the
// GET route -> cache/input-hash decides whether the graph actually reruns.
export default function RefreshButton() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  return (
    <button
      onClick={() => {
        setBusy(true);
        router.refresh();
        setTimeout(() => setBusy(false), 1200);
      }}
      className="rounded-md border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100"
    >
      {busy ? "Refreshing…" : "Refresh my brief"}
    </button>
  );
}
