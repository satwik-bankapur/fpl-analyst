"use client";

import { useEffect, useState } from "react";
import { Eyebrow } from "@/components/Chrome";

// The long wait (design §03). On the free tier the backend cold-starts, so this
// has to hold attention for up to ~50s without ever looking hung. Three devices
// do that work, all on timers rather than on real progress (the POST is a single
// opaque request — there is no progress to report, and pretending otherwise
// would be a lie):
//
//   1. staged lines that tick over on a schedule slower than the happy path,
//   2. a "while you wait" line that rotates, so the screen is never static,
//   3. an honest cold-start note that appears at 10s and explains the wait.
//
// ponytail: timers, not real progress — wire to streamed progress only if the
// brief endpoint ever grows one.

const STAGES = ["Reading your squad", "Checking fixtures, minutes and prices…", "Writing your brief"];

/** Seconds at which each stage becomes the active one. */
const STAGE_AT = [0, 3, 14];
const COLD_START_AT = 10;

const ASIDES = [
  "Most managers lose more points to the players they keep than the ones they never bought.",
  "A bench that never plays still costs you the money you spent on it.",
  "The captain pick swings more points across a season than any single transfer.",
  "Rolling a transfer is a decision, not a delay — it just rarely feels like one.",
];

export default function LoadingState({ gameweek }: { gameweek?: number | null }) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const t = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, []);

  const stage = STAGE_AT.filter((s) => elapsed >= s).length - 1;
  const aside = ASIDES[Math.floor(elapsed / 9) % ASIDES.length];

  return (
    <div className="flex flex-1 flex-col px-5 py-7 sm:px-8">
      <Eyebrow>Writing{gameweek ? ` · GW${gameweek}` : ""}</Eyebrow>
      <h2 className="mb-5 mt-3 text-[23px] font-bold leading-tight tracking-[-0.015em]">
        The scout has your squad.
      </h2>

      <div className="relative mb-6 h-0.5 overflow-hidden bg-line" role="progressbar" aria-label="Writing your brief">
        <div className="absolute left-0 top-0 h-full w-[30%] animate-sweep bg-state-blue" />
      </div>

      <ol className="m-0 flex list-none flex-col gap-3.5 p-0">
        {STAGES.map((text, i) => {
          const done = i < stage;
          const active = i === stage;
          return (
            <li key={text} className="flex items-baseline gap-3">
              <span
                aria-hidden
                className={`w-3.5 shrink-0 text-label ${
                  done ? "text-in-green" : active ? "animate-blink text-[12px] text-state-blue" : "text-ink-900"
                }`}
              >
                {done ? "✓" : active ? "●" : "○"}
              </span>
              <span
                className={`text-[15px] leading-snug ${
                  done ? "text-ink-600" : active ? "text-ink-0" : "text-ink-800"
                }`}
              >
                {text}
              </span>
            </li>
          );
        })}
      </ol>
      {/* Announced separately so screen readers get the stage change, not the ticker. */}
      <p className="sr-only" aria-live="polite">
        {STAGES[stage]}
      </p>

      <div className="mt-7 rounded-[11px] border border-line bg-surface-800 p-4">
        <Eyebrow>While you wait</Eyebrow>
        <p key={aside} className="m-0 mt-2 animate-drift text-[15px] leading-relaxed text-ink-300">
          {aside}
        </p>
      </div>

      {elapsed >= COLD_START_AT && (
        <p className="mt-auto pt-6 text-label leading-relaxed text-ink-700">
          The first brief of the day can take up to a minute — the scout has to wake up. Nothing is broken.
          Leave this open.
        </p>
      )}
    </div>
  );
}
