"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import DraftPicker from "@/components/DraftPicker";

// Mode-driven hero (§2.1). PRESEASON: "Rate my draft" -> picker modal (Stage A
// primary). IN_SEASON: team-ID input -> /brief/[teamId] (Stage B primary). The
// switch is data-driven off /api/health.mode, so the Stage B flip needs no change.
export default function HomeHero({ mode }: { mode: "PRESEASON" | "IN_SEASON" }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [teamId, setTeamId] = useState("");

  return (
    <div>
      <h1 className="text-4xl font-extrabold tracking-tight text-ink sm:text-5xl">
        Your FPL team, scouted like a pro.
      </h1>
      <p className="mt-4 max-w-xl text-lg text-slate-600">
        Paste your team — or draft one now, pre-season — and get a scout report:
        the verdict, the transfer, the captain. Free.
      </p>

      <div className="mt-8">
        {mode === "PRESEASON" ? (
          <button
            onClick={() => setOpen(true)}
            className="rounded-lg bg-pitch px-6 py-3 text-lg font-semibold text-white shadow hover:opacity-90"
          >
            Rate my draft
          </button>
        ) : (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (teamId.trim()) router.push(`/brief/${teamId.trim()}`);
            }}
            className="flex flex-col gap-2 sm:flex-row"
          >
            <input
              value={teamId}
              onChange={(e) => setTeamId(e.target.value)}
              inputMode="numeric"
              placeholder="Your FPL team ID"
              className="rounded-lg border border-slate-300 px-4 py-3"
            />
            <button className="rounded-lg bg-pitch px-6 py-3 font-semibold text-white hover:opacity-90">
              Get my free brief
            </button>
          </form>
        )}
        <p className="mt-2 text-sm text-slate-500">
          Find your ID in your FPL points-page URL.
        </p>
      </div>

      {open && <DraftPicker onClose={() => setOpen(false)} />}
    </div>
  );
}
