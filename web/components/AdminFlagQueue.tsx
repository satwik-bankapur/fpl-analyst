"use client";

import { useState } from "react";
import Link from "next/link";
import { FlagChips } from "@/components/AdminBits";
import type { FlagGroup, FlagKind, FlagQueue as Queue, QueueClub } from "@/lib/admin";

// The flagged-player list as a WORK QUEUE rather than an inventory.
//
// The old panel was a flat table of ~260 rows sorted by EP. True, and useless on a
// Sunday: it answered "which players are flagged" when the only question that
// spends an hour well is "which club should I write a note about next". So this
// groups by flag, says whether the flag is worth acting on at all, and — for the
// two flags a team note actually fixes — ranks clubs by payoff and links each
// straight to its note card.
//
// Every number here comes from `api.admin.flag_queue`; nothing is re-derived
// client-side, so the ranking on screen is the ranking the API computed.

const KIND_LABEL: Record<FlagKind, string> = {
  actionable: "Actionable",
  transient: "Transient",
  known_limitation: "Known limitation",
};

const KIND_CLASS: Record<FlagKind, string> = {
  actionable: "bg-in-greenbg text-in-green",
  transient: "bg-state-bluebg text-state-blue",
  known_limitation: "bg-surface-700 text-ink-500",
};

export default function FlagQueue({ queue }: { queue: Queue }) {
  if (queue.total_flagged_players === 0) {
    return (
      <p className="m-0 text-body text-ink-600">
        None. Every scored player is on its own data.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-5">
      <p className="m-0 text-body text-ink-600">
        {queue.total_flagged_players} players carry an honesty flag ·{" "}
        <span className={queue.clubs_with_notes === 0 ? "text-out-red" : "text-ink-300"}>
          {queue.clubs_with_notes} of {queue.clubs_total} clubs have a note written
        </span>
      </p>
      {queue.groups.map((g) => (
        <Group key={g.flag} group={g} />
      ))}
    </div>
  );
}

function Group({ group: g }: { group: FlagGroup }) {
  return (
    <section className="rounded border border-line bg-surface-900 p-4">
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <h3 className="m-0 font-mono text-body font-semibold text-ink-0">{g.flag}</h3>
        <span className={`rounded px-1.5 py-0.5 text-[11px] font-semibold uppercase ${KIND_CLASS[g.kind]}`}>
          {KIND_LABEL[g.kind]}
        </span>
        <span className="text-label tabular-nums text-ink-700">{g.count} players</span>
      </div>
      <p className="m-0 mb-3 max-w-[80ch] text-body leading-relaxed text-ink-600">
        {g.explanation}
      </p>

      {g.note_improvable ? (
        <>
          <p className="m-0 mb-2 text-label text-ink-700">
            Clubs ranked by how much a team note would improve brief quality
            (flagged players × their highest ep_next4).
          </p>
          <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
            {g.clubs.map((c) => (
              <ClubRow key={c.team_id} club={c} />
            ))}
          </ul>
        </>
      ) : (
        // Not note-improvable: no club ranking, because writing a note would not
        // move any of these numbers. Show the players and stop.
        <ul className="m-0 flex list-none flex-wrap gap-x-4 gap-y-1 p-0 text-body">
          {g.players.map((p) => (
            <li key={p.id} className="text-ink-500">
              <span className="text-ink-300">{p.web_name}</span>{" "}
              <span className="text-ink-700">{p.team_short}</span>{" "}
              <span className="tabular-nums text-ink-700">{p.ep_next4.toFixed(1)}</span>
            </li>
          ))}
          {g.count > g.players.length && (
            <li className="text-ink-700">+{g.count - g.players.length} more</li>
          )}
        </ul>
      )}
    </section>
  );
}

function ClubRow({ club: c }: { club: QueueClub }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="rounded border border-line-soft">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="text-body font-semibold text-ink-0"
        >
          <span aria-hidden className="mr-1.5 inline-block w-3 text-ink-700">
            {open ? "▾" : "▸"}
          </span>
          {c.team_short}
        </button>
        <span className="text-label text-ink-700">{c.team_name}</span>

        <span className="ml-auto flex flex-wrap items-center gap-x-3 gap-y-1">
          {c.identical_ep && (
            // The signature of a club scored entirely on baselines: its whole
            // squad's EP has collapsed onto a handful of values. The counts are
            // shown so this reads as evidence, not a verdict.
            <span
              className="rounded bg-out-redbg px-1.5 py-0.5 text-[11px] font-semibold text-out-red"
              title={`Only ${c.distinct_ep_next4} distinct ep_next4 values across ${c.scored_players} scored players — this club is being scored on baselines, not on its own data.`}
            >
              near-identical EP · {c.distinct_ep_next4}/{c.scored_players}
            </span>
          )}
          <span className="text-label tabular-nums text-ink-600">
            {c.count} flagged · top {c.top_ep_next4.toFixed(1)} · score{" "}
            <span className="font-semibold text-ink-300">{c.score.toFixed(0)}</span>
          </span>
          {c.has_note ? (
            <span className="text-label text-in-green">
              note written{c.is_sample && " (sample)"}
            </span>
          ) : (
            <Link
              href={`/admin/notes#club-${c.team_id}`}
              className="text-label font-semibold text-state-blue no-underline hover:underline"
            >
              write note →
            </Link>
          )}
        </span>
      </div>

      {open && (
        <ul className="m-0 list-none border-t border-line-soft px-3 py-2 text-body">
          {c.players.map((p) => (
            <li key={p.id} className="flex flex-wrap items-baseline gap-x-2 py-0.5">
              <span className="text-ink-300">{p.web_name}</span>
              <span className="text-ink-700">{p.position}</span>
              <span className="tabular-nums text-ink-500">£{p.price.toFixed(1)}</span>
              <span className="tabular-nums text-ink-0">{p.ep_next4.toFixed(2)}</span>
              <FlagChips flags={[p.flag]} />
              {p.news && <span className="text-label text-ink-700">{p.news}</span>}
            </li>
          ))}
          {c.count > c.players.length && (
            <li className="py-0.5 text-label text-ink-700">
              +{c.count - c.players.length} more flagged at this club
            </li>
          )}
        </ul>
      )}
    </li>
  );
}
