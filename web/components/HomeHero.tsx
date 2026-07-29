"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import DraftPicker from "@/components/DraftPicker";
import { Eyebrow } from "@/components/Chrome";

// Mode-driven hero (§2.1). PRESEASON: "Rate my draft" -> picker modal (Stage A
// primary). IN_SEASON: team-ID input -> /brief/[teamId] (Stage B primary). The
// switch is data-driven off /api/health.mode, so the Stage B flip needs no change.
//
// Design §01: one promise and one input above the fold, centred, with an eyebrow
// carrying the live gameweek state and a footnote carrying the price (free).
export default function HomeHero({
  mode,
  nextGw,
  deadline,
}: {
  mode: "PRESEASON" | "IN_SEASON";
  nextGw?: number | null;
  deadline?: string | null;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [teamId, setTeamId] = useState("");

  const when = deadline
    ? new Date(deadline).toLocaleString("en-GB", {
        weekday: "short",
        hour: "2-digit",
        minute: "2-digit",
        timeZone: "UTC",
      }) + " GMT"
    : null;

  // Only states the backend actually reported — no invented gameweek or deadline.
  const kicker =
    mode === "PRESEASON"
      ? ["Pre-season", nextGw && `GW${nextGw} deadline${when ? ` ${when}` : ""}`].filter(Boolean).join(" · ")
      : [nextGw ? `Gameweek ${nextGw}` : "This gameweek", when && `deadline ${when}`].filter(Boolean).join(" · ");

  return (
    <div className="px-5 py-8 text-center sm:px-12 sm:pb-12 sm:pt-16">
      <Eyebrow>{kicker}</Eyebrow>

      {mode === "PRESEASON" ? (
        <>
          <h1 className="mx-auto mb-2.5 mt-3.5 max-w-[26ch] text-balance text-hero font-bold text-ink-0 sm:text-display">
            Your draft, marked before it costs you
          </h1>
          <p className="mx-auto mb-5 max-w-[52ch] text-pretty text-prose leading-[1.55] text-ink-500 sm:text-[18px]">
            Build your 15 in a minute. Get a written verdict on the shape, the spend and the two picks
            you&apos;ll regret by September.
          </p>
          <button onClick={() => setOpen(true)} className="btn-primary w-full py-3.5 sm:w-auto sm:px-8">
            Rate my draft
          </button>
          <p className="m-0 mt-3 text-label text-ink-700">Free · no signup · 15 players, £100.0m</p>
        </>
      ) : (
        <>
          <h1 className="mx-auto mb-2.5 mt-3.5 max-w-[26ch] text-balance text-hero font-bold text-ink-0 sm:text-display">
            Your personal FPL analyst
          </h1>
          <p className="mx-auto mb-5 max-w-[52ch] text-pretty text-prose leading-[1.55] text-ink-500 sm:text-[18px]">
            Paste your team ID. Get a scout report on your squad — what to fix, who to captain, and why.
          </p>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (teamId.trim()) router.push(`/brief/${teamId.trim()}`);
            }}
            className="mx-auto flex max-w-[520px] gap-2"
          >
            <input
              value={teamId}
              onChange={(e) => setTeamId(e.target.value)}
              inputMode="numeric"
              aria-label="Your FPL team ID"
              placeholder="Your FPL team ID, e.g. 2417380"
              className="field flex-1"
            />
            <button className="btn-primary whitespace-nowrap px-4">Get my free brief</button>
          </form>
          <p className="m-0 mt-3.5 text-label text-ink-700">
            Free · no signup · takes 20 seconds ·{" "}
            <button onClick={() => setOpen(true)} className="text-ink-500 underline hover:text-ink-0">
              pre-season? rate a draft instead
            </button>
          </p>
        </>
      )}

      {open && <DraftPicker onClose={() => setOpen(false)} />}
    </div>
  );
}
