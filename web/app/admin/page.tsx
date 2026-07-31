"use client";

import { useCallback } from "react";
import { FlagChips, Failed, Loading, PageTitle, Panel, Stat, useLoad, timeAgo } from "@/components/AdminBits";
import FlagQueue from "@/components/AdminFlagQueue";
import { adminGet, FlagQueue as Queue, Overview, TopRow } from "@/lib/admin";

// The post-sync sanity check. Everything on this page is arranged so a bad sync
// is visible without reading carefully: a stale last_sync, an empty archive, a
// match_method spread that has collapsed onto "none", or a top-20 where every
// row is a goalkeeper (the signature of a missed season rollover — STATUS §6).

type Bundle = { overview: Overview; top: TopRow[]; flagged: Queue };

const STALE_HOURS = 36; // a daily cron that missed a run

export default function AdminDashboard() {
  const load = useCallback(
    async (): Promise<Bundle> => {
      const [overview, top, flagged] = await Promise.all([
        adminGet<Overview>("/admin/overview"),
        adminGet<TopRow[]>("/admin/top?n=20"),
        adminGet<Queue>("/admin/flag-queue"),
      ]);
      return { overview, top, flagged };
    },
    []
  );
  const { data, error, loading, reload } = useLoad<Bundle>(load, []);

  if (error) return <Failed error={error} onRetry={reload} />;
  if (loading || !data) return <Loading what="dashboard" />;

  const { overview: o, top, flagged } = data;
  const syncAgeH = o.last_sync ? (Date.now() - new Date(o.last_sync).getTime()) / 3.6e6 : Infinity;
  const archiveSeasons = Object.entries(o.archive_by_season);
  const gkHeavy = top.filter((r) => r.position === "GK").length >= 5;

  return (
    <>
      <PageTitle
        right={
          <button type="button" onClick={reload} className="btn-ghost py-2">
            Refresh
          </button>
        }
      >
        Dashboard
      </PageTitle>

      <div className="flex flex-col gap-5">
        <Panel title="State">
          <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
            <Stat label="Mode" value={o.mode} />
            <Stat
              label="Last sync"
              value={timeAgo(o.last_sync)}
              tone={syncAgeH > STALE_HOURS ? "warn" : "ok"}
            />
            <Stat label="Next GW" value={o.next_gw ?? "—"} />
            <Stat
              label="Deadline"
              value={o.next_deadline ? new Date(o.next_deadline).toLocaleString() : "—"}
            />
          </div>
        </Panel>

        <Panel title="Row counts">
          <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4 lg:grid-cols-7">
            {Object.entries(o.counts).map(([k, v]) => (
              <Stat
                key={k}
                label={k.replace(/_/g, " ")}
                value={v}
                tone={v === 0 && k !== "briefs" && k !== "signups" ? "warn" : undefined}
              />
            ))}
          </div>
        </Panel>

        <div className="grid gap-5 lg:grid-cols-2">
          <Panel title="Archive by season">
            {archiveSeasons.length === 0 ? (
              // The failure mode that does not look like one: with no archive every
              // pre-season EP collapses onto the price-band baseline.
              <p className="m-0 text-body text-out-red">
                Empty. Pre-season scoring has nothing to work from — run{" "}
                <code>python -m fpl backfill-archive</code>.
              </p>
            ) : (
              <ul className="m-0 list-none p-0 text-body">
                {archiveSeasons.map(([season, n]) => (
                  <li key={season} className="flex justify-between border-b border-line-soft py-1.5 last:border-0">
                    <span className="text-ink-300">{season}</span>
                    <span className="tabular-nums text-ink-500">{n}</span>
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          <Panel title="Archive match method">
            <ul className="m-0 list-none p-0 text-body">
              {Object.entries(o.match_method).map(([method, n]) => (
                <li key={method} className="flex justify-between border-b border-line-soft py-1.5 last:border-0">
                  <span className={method === "none" ? "text-out-red" : "text-ink-300"}>{method}</span>
                  <span className="tabular-nums text-ink-500">{n}</span>
                </li>
              ))}
            </ul>
          </Panel>
        </div>

        <Panel title={`Top 20 by ep_next4${gkHeavy ? " — goalkeeper-heavy, check the archive" : ""}`}>
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-body">
              <thead>
                <tr className="text-left text-eyebrow uppercase text-ink-700">
                  <th className="py-1.5 pr-3 font-semibold">#</th>
                  <th className="py-1.5 pr-3 font-semibold">Player</th>
                  <th className="py-1.5 pr-3 font-semibold">Club</th>
                  <th className="py-1.5 pr-3 font-semibold">Pos</th>
                  <th className="py-1.5 pr-3 text-right font-semibold">£</th>
                  <th className="py-1.5 pr-3 text-right font-semibold">ep</th>
                  <th className="py-1.5 pr-3 text-right font-semibold">ep_next4</th>
                  <th className="py-1.5 font-semibold">Flags</th>
                </tr>
              </thead>
              <tbody>
                {top.map((r, i) => (
                  <tr key={r.id} className="border-t border-line-soft">
                    <td className="py-1.5 pr-3 tabular-nums text-ink-700">{i + 1}</td>
                    <td className="py-1.5 pr-3 text-ink-0">{r.web_name}</td>
                    <td className="py-1.5 pr-3 text-ink-500">{r.team_short}</td>
                    <td className="py-1.5 pr-3 text-ink-500">{r.position}</td>
                    <td className="py-1.5 pr-3 text-right tabular-nums text-ink-500">{r.price.toFixed(1)}</td>
                    <td className="py-1.5 pr-3 text-right tabular-nums text-ink-300">{r.ep.toFixed(2)}</td>
                    <td className="py-1.5 pr-3 text-right tabular-nums text-ink-0">{r.ep_next4.toFixed(2)}</td>
                    <td className="py-1.5">
                      <FlagChips flags={r.flags} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {top.length === 0 && <p className="m-0 text-body text-out-red">No ep_scores rows — the sync has never completed.</p>}
          </div>
        </Panel>

        <Panel title="Flagged players — triage queue">
          <FlagQueue queue={flagged} />
        </Panel>
      </div>
    </>
  );
}
